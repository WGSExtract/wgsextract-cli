"""DeepVariant resumable stages, launchers and result publication."""

import contextlib
import glob
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wgsextract_cli.api.vcf import DEEPVARIANT_STEPS
from wgsextract_cli.core import native_deepvariant
from wgsextract_cli.core.alignment.cram_reference import cram_reference_cache
from wgsextract_cli.core.alignment.cram_staging import (
    region_intervals,
)
from wgsextract_cli.core.containers import (
    host_path,
    run_container,
)
from wgsextract_cli.core.reference_assets import read_fai_lengths
from wgsextract_cli.core.reference_evidence import Provenance
from wgsextract_cli.core.resource_estimates import (
    PostprocessPlan,
)
from wgsextract_cli.core.steps import Decision, Step, StepJournal
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_files import ensure_vcf_indexed

from ..reference_evidence import publish_gvcf
from .models import _container_regions, _dv_env
from .resources import _postprocess_args

_OOM_ERROR = (
    "DeepVariant ran out of memory: a process in its container was OOM-killed, "
    "so the run was stopped rather than left waiting on the killed worker. "
    "Free memory (close other jobs) or lower --threads, then rerun."
)


def _docker_args(
    model_type: str,
    ref: str,
    reads: str,
    vcf: str,
    shards: int,
    work: Path,
    region: str | None,
    checkpoint: str | None,
    batch_size: int | None,
    postprocess: PostprocessPlan | None,
    threads: int,
    gvcf: str | None = None,
) -> list[str]:
    """run_deepvariant's options, with the paths an image sees."""
    args = [
        "--model_type", model_type,
        "--ref", host_path(ref),
        "--reads", host_path(reads),
        "--output_vcf", host_path(vcf),
        *(["--output_gvcf", host_path(gvcf)] if gvcf else []),
        "--num_shards", str(shards),
        "--intermediate_results_dir", host_path(str(work)),
        *(["--regions", _container_regions(region)] if region else []),
    ]  # fmt: skip
    if checkpoint:
        args += ["--customized_model", host_path(checkpoint)]
    if batch_size is not None:
        args += ["--call_variants_extra_args", f"batch_size={batch_size}"]
    return args + _postprocess_args(postprocess, threads)


def _native_args(
    native: native_deepvariant.NativeDeepVariant,
    ref: str,
    reads: str,
    vcf: str,
    shards: int,
    work: Path,
    region: str | None,
    batch_size: int | None,
    gpu: bool,
    threads: int,
    gvcf: str | None = None,
) -> list[str]:
    """wgse-deepvariant run's options. On Linux call_variants runs on the CPU,
    or with ``gpu`` (--gpu) on an NVIDIA GPU, never silently on the CPU
    instead. A selection goes to it as one BED of
    the intervals it covers, in reference order (BED files, literals or
    both, of any size: a command line holds at most 1 MiB on macOS), named
    by its content so a changed selection reruns the stages."""
    args = [
        "run",
        f"--model_type={native.model_type}",
        f"--ref={ref}",
        f"--reads={reads}",
        f"--output_vcf={vcf}",
        *([f"--output_gvcf={gvcf}"] if gvcf else []),
        f"--num_shards={shards}",
        f"--intermediate_results_dir={work}",
        f"--checkpoint={native.checkpoint.resolve()}",
        f"--small_model_path={native.small_model.resolve()}",
    ]
    if region:
        lengths = read_fai_lengths(ref)
        intervals = region_intervals(region, lengths, "DeepVariant")
        bed = "".join(
            f"{chrom}\t{start}\t{end}\n"
            for chrom in lengths
            for start, end in intervals.get(chrom, ())
        )
        path = work / _REGIONS / f"{hashlib.sha256(bed.encode()).hexdigest()[:16]}.bed"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bed, newline="\n")
        args.append(f"--regions={path}")
    if batch_size is not None:
        args.append(f"--batch_size={batch_size}")
    if sys.platform != "darwin":
        # This run's threads: the shard count a resumed run keeps may differ.
        args += [
            f"--inference_backend={'cuda' if gpu else 'cpu'}",
            f"--cpu_threads={threads}",
        ]
    return args


STEPS = DEEPVARIANT_STEPS
WORK_DIRECTORY = ".deepvariant.work"
# --reference-evidence gvcf: DeepVariant's gVCF, written by the same run.
GVCF_NAME = "deepvariant.g.vcf.gz"
_COMMAND_MARK = "***** Running the command:*****"
# Google's stage tools (/opt/deepvariant/bin/make_examples) or wgse-deepvariant's
# stage subcommands (wgse-deepvariant make_examples).
_STAGE_BINARY = re.compile(r"[/ ](make_examples|call_variants|postprocess_variants)\s")
# Settings that bound a stage's memory or CPU use but not what it writes.
_RESOURCE_FLAGS = re.compile(
    r'\s--(?:cpus|num_partitions|batch_size|cpu_threads)[= ]"?\d+"?'
)
_JOBLOG = ".make_examples.joblog"
# wgse-deepvariant's selection BED, beside (not among) the stage outputs.
_REGIONS = "regions"
_DV_BIN = "/opt/deepvariant/bin"


def _migrate_image(
    journal: StepJournal, image: str, recorded: dict[str, str | None]
) -> None:
    """Read records of ``image`` from v0.8.5 to the release before #601, which
    appended Docker's image ID to it (``image@sha256:ID``), as ``recorded``,
    so their finished stages are kept. A tag whose ID has since changed was
    replaced, so its stages still rerun; a digest pin names its build (#597)."""
    legacy = re.compile(re.escape(image) + r"@(sha256:[0-9a-f]{64})")
    current_id = recorded.get("image_id")

    def migrate(params: dict[str, Any]) -> dict[str, Any]:
        match = legacy.fullmatch(str(params.get("image", "")))
        if match and "image_id" not in params:
            if current_id is None and "@" not in image:
                return params
            if current_id is None or match.group(1) == current_id:
                params.update(recorded)
        return params

    for stage in STEPS:
        journal.migrate_params(stage, migrate)


def _shard_count(journal: StepJournal, threads: int) -> int:
    """make_examples' shard count: an earlier run's, so a rerun with other
    --threads keeps its finished shards and stages (--threads then sets only
    how many run at once), unless steps are forced to rerun."""
    shards = journal.recorded_params("make_examples").get("shards")
    if not isinstance(shards, int) or any(journal.policy.forced(s) for s in STEPS):
        return threads
    if shards != threads:
        logging.info(
            f"Keeping the {shards} make_examples shards of the earlier run; "
            f"{threads} run at once."
        )
    return shards


def _checkpoint_files(checkpoint: str | None) -> dict[str, str]:
    """The files of a --checkpoint (a model directory, or a checkpoint prefix
    and the files beside it), by input role."""
    if not checkpoint:
        return {}
    path = Path(checkpoint)
    if path.is_dir():
        files = [p for p in path.rglob("*") if p.is_file()]
    else:
        files = list(path.parent.glob(glob.escape(path.name) + "*"))
        files.append(path.parent / "example_info.json")
    return {
        f"checkpoint:{p.relative_to(path if path.is_dir() else path.parent)}": str(p)
        for p in sorted(set(files))
        if p.is_file()
    }


class _Launcher:
    """Runs DeepVariant's run_deepvariant, or one of the stage commands it
    prints, on this machine or in its image."""

    def __init__(
        self,
        local: str | None,
        image: str | None,
        arm64: bool,
        paths: list[str | None],
        work: Path,
        gpu_check: Callable[[], None] | None = None,
        preflight: Callable[[], None] | None = None,
        cram_reference: tuple[str, str] | None = None,
    ):
        self.local = local
        self.image = image
        self.arm64 = arm64
        self.paths = paths
        self.work = work
        # A GPU image's check of the GPU, before the first stage that uses it.
        self.gpu_check = gpu_check
        # A check before the first stage that runs.
        self.preflight = preflight
        self.cram_reference = cram_reference

    def ready(self) -> None:
        """Run the preflight check once, before a stage runs."""
        if self.preflight is not None:
            check, self.preflight = self.preflight, None
            check()

    def launcher(self) -> str:
        if self.cram_reference and self.local:
            return str(Path(self.local).resolve())
        return str(self.local) if not self.image else f"{_DV_BIN}/run_deepvariant"

    def __call__(
        self,
        argv: list[str],
        *,
        capture: bool = False,
        gpu: bool = False,
        reads: bool = False,
    ) -> str:
        """Run ``argv``; ``gpu`` gives a GPU image's container the GPUs."""
        gpu = gpu and self.gpu_check is not None
        if gpu:
            assert self.gpu_check is not None
            self.gpu_check()
            self.gpu_check = lambda: None
        if not self.image:
            # stdin closed: DeepVariant's GNU parallel would read it otherwise.
            reference = (
                cram_reference_cache(*self.cram_reference, self.work)
                if reads and self.cram_reference
                else contextlib.nullcontext(None)
            )
            with reference as cache:
                env = _dv_env()
                if cache is not None:
                    # The native reader ignores --ref for decoding (#1061).
                    # A relative cache path avoids interpreting ':' and '%s'
                    # in the user's output directory as htslib search syntax.
                    env.update(REF_PATH=f"{cache.name}/%s", REF_CACHE="")
                result = run_command(
                    argv,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=capture,
                    cwd=self.work if cache is not None else None,
                )
            return (result.stdout or "") if capture else ""
        if not capture:
            # Its temporary files stay out of the stage outputs beside them.
            return self._in_image(argv, self.work / ".tmp", capture, gpu)
        # Planning (also in --dry-run, which takes no lock) gets its own
        # temporary folder, away from a running stage's.
        self.work.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".plan-", dir=self.work))
        try:
            return self._in_image(argv, tmp, capture)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _in_image(
        self, argv: list[str], tmp: Path, capture: bool, gpu: bool = False
    ) -> str:
        assert self.image is not None
        tmp.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {
            "gpus": gpu,
            "temporary_directory": str(tmp),
            "oom_error": None if capture else _OOM_ERROR,
            "capture_output": capture,
        }
        if self.arm64:
            # Bypass community entrypoints that auto-select INT8/BF16.
            result = run_container(
                self.image, argv[1:], self.paths,
                entrypoint=argv[0], local_only=True, **options,
            )  # fmt: skip
        else:
            result = run_container(self.image, argv, self.paths, **options)
        return (result.stdout or "") if capture else ""


def _stage_commands(plan: str) -> dict[str, list[str]]:
    """The shell commands run_deepvariant --dry_run=true printed, by stage.
    Commands after postprocess_variants (reports) belong to it."""
    stages: dict[str, list[str]] = {}
    stage = None
    for chunk in plan.split(_COMMAND_MARK)[1:]:
        command = chunk.strip().split("\n\n", 1)[0].strip()
        if match := _STAGE_BINARY.search(command):
            stage = match.group(1)
        if stage is None:
            break
        stages.setdefault(stage, []).append(command)
    if list(stages) != list(STEPS[1:]):
        raise WGSExtractError(
            "Couldn't read DeepVariant's make_examples, call_variants and "
            "postprocess_variants commands from run_deepvariant --dry_run=true "
            f"(found: {', '.join(stages) or 'none'})."
        )
    return stages


def _stage_files(work: Path, claimed: set[Path]) -> list[Path]:
    """The stage outputs in ``work`` that no earlier stage wrote."""
    if not work.is_dir():
        return []
    return sorted(
        path
        for path in work.iterdir()
        if path.is_file() and not path.name.startswith(".") and path not in claimed
    )


def _run_stages(
    journal: StepJournal,
    run: _Launcher,
    dv_args: list[str],
    *,
    inputs: dict[str, str | None],
    params: dict[str, Any],
    region: str | None,
    jobs: int,
    staging: Callable[[], str] | None,
    out_vcf: str,
    evidence: tuple[str, Provenance] | None = None,
    preflight: Callable[[bool], None] | None = None,
) -> bool:
    """Run DeepVariant stage by stage, skipping the ones an earlier run
    finished. A killed make_examples keeps its finished shards, and runs
    ``jobs`` of them at once. ``evidence``: the gVCF postprocess_variants
    also publishes, and its provenance. ``preflight`` is called once the
    stages are known, with whether none of them is kept. Returns whether
    the VCF was made or kept (not in --dry-run)."""
    work = run.work
    work.mkdir(parents=True, exist_ok=True)
    try:
        plan = run([run.launcher(), *dv_args, "--dry_run=true"], capture=True)
    finally:
        if journal.dry_run:
            shutil.rmtree(work / _REGIONS, ignore_errors=True)
            with contextlib.suppress(OSError):
                work.rmdir()
    commands = _stage_commands(plan)
    staged_vcf = work / Path(out_vcf).name
    # The gVCF and its index are the final stage's outputs too, so a missing
    # one reruns it; without --reference-evidence its record is as before.
    final_outputs = [out_vcf]
    final_params: dict[str, Any] = {}
    if evidence:
        final_outputs += [evidence[0], evidence[0] + ".tbi"]
        # The provenance (region, ploidy exclusions) can change while the
        # stage commands don't (a woman's chrY is never called).
        header = evidence[1].header().encode()
        final_params["reference_evidence"] = hashlib.sha256(header).hexdigest()

    def step(name: str) -> Step:
        # A stage's key holds its own and every earlier stage's commands, so
        # even the final VCF, kept after the others are deleted, is redone
        # when an earlier stage's settings (such as the model) change.
        upto = STEPS[1 : STEPS.index(name) + 1]
        text = "\n".join(c for stage in upto for c in commands[stage])
        last = name == "postprocess_variants"
        return Step(
            name,
            inputs=inputs,
            outputs=final_outputs if last else (),
            params={
                **params,
                "commands": _RESOURCE_FLAGS.sub("", text),
                **(final_params if last else {}),
            },
        )

    staging_step = Step(
        "stage_reads",
        inputs={
            role: path
            for role, path in inputs.items()
            if role in ("alignment", "reference") or role.startswith("regions:")
        },
        outputs=[work / "reads" / "reads.cram", work / "reads" / "reads.cram.crai"],
        params={"region": region},
        tools=["samtools"],
    )
    stages = [
        staging_step if name == "stage_reads" else step(name)
        for name in STEPS
        if name != "stage_reads" or staging is not None
    ]
    if preflight is not None:
        checked: list[Decision] = []
        for current in stages:
            checked.append(journal.check(current, after=checked))
        preflight(all(d.run and not d.partial for d in checked))
    final = stages[-1]
    if not any(journal.policy.forced(s) for s in STEPS[:-1]):
        if not journal.check(final).run:
            journal.decide(final)
            if not journal.dry_run:
                ensure_vcf_indexed(out_vcf)
            return not journal.dry_run

    decisions: list[Decision] = []
    claimed: set[Path] = set()
    for current in stages:
        name = current.name
        decision = journal.decide(current, after=decisions)
        decisions.append(decision)
        if journal.dry_run:
            continue
        if not decision.run:
            claimed |= {journal.resolve(item["path"]) for item in decision.outputs}
            continue
        # A make_examples killed with the same inputs and settings keeps the
        # shards it finished (GNU parallel's job log); anything else restarts.
        resume = name == "make_examples" and decision.partial
        if resume and not any("parallel " in c for c in commands[name]):
            # wgse-deepvariant's make_examples is one process: no shard log.
            logging.info(
                "Rerunning make_examples in full: wgse-deepvariant can't keep "
                "the shards an interrupted run finished."
            )
            resume = False
        if not resume:
            for stale in _stage_files(work, claimed):
                stale.unlink()
            with contextlib.suppress(FileNotFoundError):
                (work / _JOBLOG).unlink()
        run.ready()
        with journal.running(decision, keep_partial=resume) as handle:
            if name == "stage_reads":
                assert staging is not None
                (work / "reads").mkdir(exist_ok=True)
                staging()
                continue
            for command in commands[name]:
                if name == "make_examples":
                    # GNU parallel logs each finished shard; --resume-failed
                    # then runs only the shards a killed run didn't finish.
                    joblog = (
                        f'--jobs {jobs} --joblog "{host_path(str(work / _JOBLOG))}"'
                    )
                    if resume:
                        logging.info(
                            "Resuming make_examples: keeping the shards an "
                            "interrupted run finished."
                        )
                        joblog += " --resume-failed"
                    command = command.replace("parallel ", f"parallel {joblog} ", 1)
                run(
                    ["/bin/bash", "-c", command],
                    gpu=name == "call_variants",
                    reads=name == "make_examples",
                )
            if name == "postprocess_variants":
                for suffix in ("", ".tbi"):
                    source = Path(str(staged_vcf) + suffix)
                    if source.exists():
                        os.replace(source, out_vcf + suffix)
                if not Path(out_vcf).exists():
                    raise WGSExtractError("DeepVariant failed to produce output VCF.")
                ensure_vcf_indexed(out_vcf)
                if evidence:
                    staged_gvcf = work / GVCF_NAME
                    if not staged_gvcf.exists():
                        raise WGSExtractError("DeepVariant didn't write its gVCF.")
                    publish_gvcf(str(staged_gvcf), *evidence)
                continue
            outputs = _stage_files(work, claimed)
            for path in outputs:
                handle.add_output(path)
            claimed |= set(outputs)
    if journal.dry_run:
        return False
    for name in STEPS[:-1]:
        journal.retire(name)
    shutil.rmtree(work, ignore_errors=True)
    return True
