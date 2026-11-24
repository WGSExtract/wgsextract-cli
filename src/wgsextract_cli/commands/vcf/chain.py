import json
import logging
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from wgsextract_cli import __version__
from wgsextract_cli.api.vcf import VcfChainAnnotateParams, VcfTableParams
from wgsextract_cli.core._confidence_rules import thresholds_from
from wgsextract_cli.core.artifacts import ChildRun, ended_as, lineage_args
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.config import config_args
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.output_checks import vcf_record_count
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.ref_library.assets import input_stem
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary
from wgsextract_cli.core.results import MANIFEST_SUFFIX, current_result, warn
from wgsextract_cli.core.steps import Step, StepJournal, resume_policy
from wgsextract_cli.core.table_writer import check_format
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    get_resource_threads,
    run_command,
)
from wgsextract_cli.core.variant_files import calculate_bam_md5, ensure_vcf_indexed
from wgsextract_cli.core.workspace_activity import forward_workspace_admission

from .._vep_engines import VepEngine, configured_engine
from .._vep_run import vep_output_paths
from .basic import record_variants, require_vcf_input, vcf_input_handler
from .chain_scope import select_records, vcf_indexes
from .clinvar_revel import CLINVAR_ANNOTATED, REVEL_ANNOTATED
from .deleteriousness import CADD_ANNOTATED, GERP_ANNOTATED
from .population import GNOMAD_ANNOTATED, PHYLOP_ANNOTATED
from .splicing import ALPHAMISSENSE_ANNOTATED, PHARMGKB_ANNOTATED, SPLICEAI_ANNOTATED
from .table import run_table

_JOURNAL = "chain_annotate.steps.json"

# annotation -> (ReferenceLibrary attribute holding its data, step output name)
_VCF_STEPS = {
    "clinvar": ("clinvar_vcf", CLINVAR_ANNOTATED),
    "revel": ("revel_file", REVEL_ANNOTATED),
    "phylop": ("phylop_file", PHYLOP_ANNOTATED),
    "cadd": ("cadd_file", CADD_ANNOTATED),
    "gerp": ("gerp_file", GERP_ANNOTATED),
    "gnomad": ("gnomad_vcf", GNOMAD_ANNOTATED),
    "spliceai": ("spliceai_vcf", SPLICEAI_ANNOTATED),
    "alphamissense": ("alphamissense_vcf", ALPHAMISSENSE_ANNOTATED),
    "pharmgkb": ("pharmgkb_vcf", PHARMGKB_ANNOTATED),
}


def _step_command(ann: str) -> list[str]:
    if ann == "vep":
        return ["vep", "run"]
    return ["vcf", ann]


def _step_output(ann: str, step_input: str, step_outdir: str) -> str:
    if ann == "vep":
        return vep_output_paths(step_input, step_outdir, "vcf")[1]
    return str(Path(step_outdir, _VCF_STEPS[ann][1]))


def _forward_step_warnings(step: str, ann: str, step_outdir: str) -> None:
    """Repeat what a step warned about its annotation data (``annotation_*``,
    such as resource records it excluded): its log was captured and its folder
    is removed. Findings about the sample stay with the step's own command."""
    for manifest in sorted(Path(step_outdir).glob(f"*{MANIFEST_SUFFIX}")):
        if manifest.name.endswith(".failed" + MANIFEST_SUFFIX):
            continue  # an earlier, failed attempt's
        for item in json.loads(manifest.read_text(encoding="utf-8"))["warnings"]:
            if item["code"].startswith("annotation_"):
                warn(
                    item["code"],
                    f"{step} '{ann}': {item['message']}",
                    **item["details"],
                )


def dataset(ann: str, lib: ReferenceLibrary) -> str | None:
    """The reference data an annotation step reads: a file, or VEP's cache
    directory; None when it isn't installed."""
    # chain-annotate is meant to run offline, so VEP also needs its cache: the
    # configured engine's (vep-rs keeps its own in the cache's vep-rs folder).
    if ann == "vep":
        if not lib.vep_cache:
            return None
        cache = vep_step_engine(lib).cache_root(Path(lib.vep_cache))
        return str(cache) if cache.is_dir() else None
    return getattr(lib, _VCF_STEPS[ann][0], None) or None


def _step_resource_args(ann: str, lib: ReferenceLibrary) -> list[str]:
    if ann == "vep":
        assert lib.vep_cache is not None
        # Pin the same offline engine and cache the journal tracks: standalone
        # VEP also looks in ~/.vep when it automatically selects an engine.
        return ["--vep-cache", lib.vep_cache, "--engine", vep_step_engine(lib).name]
    resource = dataset(ann, lib)
    if not resource:
        return []
    flag = "--am-file" if ann == "alphamissense" else f"--{ann}-file"
    args = [flag, resource]
    if ann == "cadd" and lib.cadd_indel_file:
        args += ["--cadd-indel-file", lib.cadd_indel_file]
    return args


def vep_step_engine(lib: ReferenceLibrary) -> VepEngine:
    """The engine the VEP step's `vep run` will use with this library's cache."""
    return configured_engine([Path(lib.vep_cache)] if lib.vep_cache else [])


def skip_reason(ann: str, lib: ReferenceLibrary) -> str | None:
    """Why an annotation step can't run here, or None if it can."""
    tool = "bcftools"
    if ann == "vep":
        tool = "vep-rs" if vep_step_engine(lib).name == "vep-rs" else "vep"
    if not get_tool_path(tool):
        return f"{tool} is not installed."
    if not dataset(ann, lib):
        return (
            "required reference data not found. "
            f"Run 'wgsextract ref {ann}' to download."
        )
    if ann == "clinvar" and not getattr(lib, "fasta", None):
        return "matching reference FASTA not found; pass --ref with an indexed FASTA."
    return None


def _annotations(params: VcfChainAnnotateParams) -> list[str]:
    annotations = [
        a.strip().lower() for a in params.annotations.split(",") if a.strip()
    ]
    if not annotations:
        raise WGSExtractError("No valid annotations provided in --annotations.")
    unknown = [a for a in annotations if a != "vep" and a not in _VCF_STEPS]
    if unknown:
        raise WGSExtractError(
            f"Unknown annotation type(s): {', '.join(unknown)}. Valid: "
            f"{', '.join([*_VCF_STEPS, 'vep'])}"
        )

    return annotations


def _annotation_step(
    ann: str,
    position: int,
    source: str,
    output: str,
    lib: ReferenceLibrary,
) -> Step:
    inputs: dict[str, str | None] = {
        "vcf": source,
        "data": dataset(ann, lib),
        "reference": lib.fasta,
    }
    if lib.fasta:
        for ext in (".fai", ".gzi"):
            if Path(lib.fasta + ext).is_file():
                inputs[ext[1:]] = lib.fasta + ext
    if ann == "cadd":
        inputs["indel_data"] = lib.cadd_indel_file
    settings: dict[str, object] = {"wgsextract": __version__}
    tools = ["bcftools", "bgzip", "tabix"]
    if ann == "clinvar":
        settings["confidence"] = asdict(thresholds_from())
    if ann == "vep":
        engine = vep_step_engine(lib)
        settings.update(engine=engine.name, build=lib.build)
        tools.append(engine.tool)
    return Step(
        f"{ann}:{position}",
        inputs=inputs,
        outputs=[output],
        params=settings,
        tools=tools,
        version=2 if ann == "vep" else 1,
    )


def _invoke_annotation(command: list[str], ann: str, step: str, output: str) -> None:
    try:
        # Captured so chained runs stay readable; surfaced on failure.
        res = run_command(forward_workspace_admission(command), capture_output=True)
    except subprocess.CalledProcessError as e:
        for line in (e.stderr or "").strip().splitlines():
            logging.error(f"  [{ann} ERROR] {line}")
        raise WGSExtractError(f"{step} Annotation step '{ann}' failed.") from e
    if not Path(output).is_file():
        for line in (res.stderr or "").strip().splitlines():
            logging.error(f"  [{ann} STDERR] {line}")
        raise WGSExtractError(f"{step} '{ann}' did not produce {output}.")


def _record_step_run(
    ann: str,
    result_file: Path,
    reason: str,
    outcome: Literal["ok", "failed", "cancelled"],
) -> ChildRun:
    try:
        document = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = None
    result_file.unlink(missing_ok=True)
    if (
        outcome != "ok"
        and isinstance(document, dict)
        and document.get("status") == "ok"
    ):
        # The annotator exited successfully but its expected output is missing.
        document["status"] = outcome
    return current_result().run.add_child(
        ann,
        document,
        command=" ".join(_step_command(ann)),
        reason=reason,
        outcome=outcome,
    )


def _run_annotation(
    journal: StepJournal,
    step: Step,
    ann: str,
    label: str,
    source: str,
    output: str,
    args: list[str],
) -> None:
    decision = journal.decide(step)
    if decision.run:
        if absolute_path(output) == absolute_path(source):
            raise UsageError(f"Annotation step '{ann}' would overwrite its input.")
        with journal.running(decision) as handle:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).unlink(missing_ok=True)
            for index in vcf_indexes(output):
                index.unlink()
            result_file = Path(output).parent / "step.result.json"
            result_file.unlink(missing_ok=True)
            try:
                _invoke_annotation(
                    [*args, "--result-file", str(result_file)], ann, label, output
                )
            except BaseException as error:
                _record_step_run(ann, result_file, decision.reason, ended_as(error))
                raise
            child = _record_step_run(ann, result_file, decision.reason, "ok")
            handle.metrics["result_runs"] = [
                {"run": child.run, "command": child.command}
            ]
            for index in vcf_indexes(output):
                handle.add_output(index)
    else:
        current_result().run.record_step(
            ann, decision.metrics, decision.reason, "reused"
        )
    _forward_step_warnings(label, ann, str(Path(output).parent))


def _scope_step(
    journal: StepJournal,
    name: str,
    source: str,
    outdir: Path,
    params: VcfChainAnnotateParams,
    *,
    whole_contigs: bool = False,
) -> str:
    # VEP infers structural-variant mode from the input stem.
    output = str(outdir / f"{input_stem(source)}.vcf.gz")
    step = Step(
        name,
        inputs={
            "vcf": source, "bed": params.bed,
            **{f"index:{p.name}": p for p in vcf_indexes(source)},
        },
        outputs=[output],
        params={"region": params.region, "whole_contigs": whole_contigs},
        tools=["bcftools", "tabix"],
    )  # fmt: skip
    decision = journal.decide(step)
    if decision.run:
        if absolute_path(output) == absolute_path(source):
            raise UsageError("Region selection would overwrite its input.")
        with journal.running(decision) as handle:
            outdir.mkdir(parents=True, exist_ok=True)
            with atomic_output(output) as staged:
                select_records(
                    source, staged, params.region, params.bed,
                    get_resource_threads(params.threads, stage="compression"),
                    whole_contigs=whole_contigs,
                )  # fmt: skip
                ensure_vcf_indexed(staged)
            for index in vcf_indexes(output):
                handle.add_output(index)
    return output


def _requires_contig_context(ann: str, lib: ReferenceLibrary) -> bool:
    if not lib.fasta or ann in {"gerp", "vep"}:
        return False
    resource = dataset(ann, lib)
    return not (
        ann == "phylop" and resource and resource.lower().endswith((".bw", ".bigwig"))
    )


def _work_dir(path: Path, protected: list[Path], cleanup: list[Path]) -> Path:
    resolved = path.resolve()
    if path.is_symlink() or any(
        source.is_relative_to(path) or source.resolve().is_relative_to(resolved)
        for source in protected
    ):
        raise UsageError(
            f"The chain needs its own workspace at {path}; choose another --outdir."
        )
    cleanup.append(path)
    return path


def _run_steps(
    journal: StepJournal,
    params: VcfChainAnnotateParams,
    annotations: list[str],
    source: str,
    outdir: Path,
    lib: ReferenceLibrary,
    child_args: list[str],
    step_dirs: list[Path],
) -> str:
    ready: list[tuple[int, str]] = []
    for position, ann in enumerate(annotations, start=1):
        if reason := skip_reason(ann, lib):
            warn(
                "annotation_skipped",
                f"[{position}/{len(annotations)}] Skipping '{ann}': {reason}",
                annotation=ann,
                reason=reason,
            )
        else:
            ready.append((position, ann))
    if not ready:
        raise WGSExtractError(
            "No annotation was applied: every requested step was skipped."
        )

    protected = [
        absolute_path(path)
        for path in (
            source,
            params.bed,
            lib.fasta,
            *(dataset(ann, lib) for _, ann in ready),
        )
        if path is not None
    ]
    if any(ann == "cadd" for _, ann in ready) and lib.cadd_indel_file:
        protected.append(absolute_path(lib.cadd_indel_file))

    last_context_step = 0
    if params.region is not None or params.bed is not None:
        current_result().set_details(
            {
                "scope": {
                    "region": params.region,
                    "bed": str(params.bed) if params.bed else None,
                }
            }
        )
        # Left alignment can move an indel arbitrarily far within its contig;
        # ClinVar can also compose MNVs from SNVs outside the requested span.
        last_context_step = max(
            (i for i, ann in ready if _requires_contig_context(ann, lib)), default=0
        )
        scope_dir = _work_dir(outdir / "chain_select", protected, step_dirs)
        source = _scope_step(
            journal, "select", source, scope_dir, params,
            whole_contigs=bool(last_context_step),
        )  # fmt: skip

    reference = lib.fasta or params.ref
    for position, ann in ready:
        step_dir = _work_dir(
            outdir / f"chain_step_{position}_{ann}", protected, step_dirs
        )
        output = _step_output(ann, source, str(step_dir))
        args = [
            *child_args, *_step_command(ann),
            "--input", source, "--outdir", str(step_dir),
        ]  # fmt: skip
        args += [
            "--threads",
            str(params.threads) if params.threads is not None else "auto",
        ]
        if reference:
            args += ["--ref", str(reference)]
        args += _step_resource_args(ann, lib)
        args += lineage_args(current_result().run, ann)
        _run_annotation(
            journal, _annotation_step(ann, position, source, output, lib),
            ann, f"[{position}/{len(annotations)}]", source, output, args,
        )  # fmt: skip
        source = output
        if position == last_context_step:
            trim_dir = _work_dir(outdir / "chain_trim", protected, step_dirs)
            source = _scope_step(journal, "trim", source, trim_dir, params)
    current_result().add_metrics(
        annotations_applied=",".join(ann for _, ann in ready),
        annotations_skipped=len(annotations) - len(ready),
    )
    return source


def run_chain_annotate(
    params: VcfChainAnnotateParams, keep_awake: bool | None = None
) -> None:
    verify_dependencies(["bcftools", "tabix"])
    annotations = _annotations(params)
    table_format = params.table_format or "tsv"
    if params.to_table:
        check_format(table_format)
    input_file = require_vcf_input(params)
    outdir = absolute_path(params.outdir or absolute_path(input_file).parent)
    final_out = outdir / "chain_annotated.vcf.gz"
    if absolute_path(input_file) == final_out or (
        final_out.exists()
        and Path(input_file).exists()
        and final_out.samefile(input_file)
    ):
        raise UsageError(
            "vcf chain-annotate would overwrite its input; choose another --outdir."
        )
    current_result().expect_output(final_out)
    md5_sig = (
        calculate_bam_md5(input_file, None)
        if input_file.lower().endswith((".bam", ".cram"))
        else None
    )
    lib = ReferenceLibrary(params.ref, md5_sig, input_path=input_file)
    child_args = [
        sys.executable, "-m", "wgsextract_cli.main",
        *config_args(keep_awake=keep_awake),
    ]  # fmt: skip

    logging.info(f"Starting chained annotation with: {', '.join(annotations)}")
    step_dirs: list[Path] = []
    with StepJournal(
        outdir / _JOURNAL, params.COMMAND, resume_policy(params)
    ) as journal:
        try:
            source = _run_steps(
                journal,
                params,
                annotations,
                input_file,
                outdir,
                lib,
                child_args,
                step_dirs,
            )
            with atomic_output(final_out) as staged:
                shutil.copy2(source, staged)
                for ext in (".tbi", ".csi"):
                    if Path(source + ext).exists():
                        shutil.copy2(source + ext, staged + ext)
            logging.info(f"Chain annotation complete: {final_out}")
            record_variants(
                str(final_out), "annotated_vcf", vcf_record_count(final_out)
            )
            if params.to_table:
                run_table(
                    VcfTableParams(
                        input=final_out, outdir=outdir, table_format=table_format
                    )
                )
        except BaseException:
            if step_dirs:
                logging.warning("Preserving chain annotation intermediates for resume.")
            raise
        if not params.keep_intermediates:
            for step_dir in step_dirs:
                shutil.rmtree(step_dir, ignore_errors=True)


cmd_chain_annotate = vcf_input_handler(
    VcfChainAnnotateParams, run_chain_annotate, keep_awake="keep_awake"
)
