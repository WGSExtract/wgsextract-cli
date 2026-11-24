"""Clair3 small-variant calling for Oxford Nanopore reads (optional caller)."""

import logging
import platform
import re
import subprocess
import sys
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.vcf import VcfClair3Params
from wgsextract_cli.core.alignment.cram_staging import (
    needs_cram30,
    stage_cram30,
    staging_bytes,
)
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.alignment.samtools_commands import alignment_index
from wgsextract_cli.core.atomic_output import safe_output
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.containers import (
    image_record,
    link_chain,
    require_image,
    run_container,
)
from wgsextract_cli.core.dependency_checks import (
    get_tool_version,
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.genotype_qc import (
    AUTOSOMES,
    CHR_X,
    CHR_Y,
    contigs_with_reads,
    covered_depths,
    region_targets,
)
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.output_checks import warn_if_no_variants
from wgsextract_cli.core.parabricks import gpu_memory, verify_gpu_docker
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.ref_library.assets import read_fai_lengths
from wgsextract_cli.core.region_selection import Intervals, selected_intervals
from wgsextract_cli.core.resource_estimates import (
    CLAIR3_GVCF_TIME_FACTOR,
    CLAIR3_MEMORY,
    HostRequirement,
    calling_hours,
    check_host_requirement,
    estimate_clair3,
    report_resources,
)
from wgsextract_cli.core.results import warn
from wgsextract_cli.core.space_free_paths import (
    READS_INDEXES,
    REFERENCE_INDEXES,
    restore_vcf_paths,
    space_free_paths,
)
from wgsextract_cli.core.temp_directory import scratch_directory
from wgsextract_cli.core.tool_catalog import clair3_host_supported
from wgsextract_cli.core.ultima import read_groups
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command
from wgsextract_cli.core.variant_files import ensure_vcf_indexed

from .basic import CallerSetup, plain_reference, record_variants, setup_caller
from .callers import write_region_bed
from .clair3_models import (
    MODEL_FILES as MODEL_FILES,
)
from .clair3_models import (
    Chemistry as Chemistry,
)
from .clair3_models import (
    ModelMissing as ModelMissing,
)
from .clair3_models import (
    basecaller_chemistries as basecaller_chemistries,
)
from .clair3_models import (
    bundled_models_dir as bundled_models_dir,
)
from .clair3_models import (
    check_model_matches as check_model_matches,
)
from .clair3_models import (
    choose_model as choose_model,
)
from .clair3_models import (
    model_chemistry as model_chemistry,
)
from .clair3_models import (
    model_evidence as model_evidence,
)
from .clair3_models import (
    model_for_chemistry as model_for_chemistry,
)
from .clair3_models import (
    pinned_models as pinned_models,
)
from .clair3_models import (
    resolve_model as resolve_model,
)
from .clair3_models import (
    select_model as select_model,
)
from .clair3_models import (
    verify_model as verify_model,
)
from .clair3_runtime import (
    CLAIR3,
    GPU_CLAIR3,
    MpsSetup,
    clair3_command,
    clair3_env,
    verify_mps,
)

# HKU's CUDA build of the same Clair3 for --gpu, pinned to the build validated
# in #639: Bioconda's Linux Clair3 depends on pytorch-cpu, so its --use_gpu
# falls back to the CPU.
DEFAULT_GPU_IMAGE = (
    "hkubal/clair3:v2.0.5_gpu"
    "@sha256:3f660a693142db0aa8361a58fb6379fc6e3eeead20916eaae5614bd00d21cbeb"
)
DEFAULT_GPU_VERSION = (2, 0, 5)
_CUDA_PROBE = "import torch; print(torch.cuda.is_available())"
# Clair3 starts a full-alignment GPU worker only on a GPU with this much free
# memory (CallVariantsFromCffiGPU); pileup workers need 5000 MiB.
GPU_FREE_MIB = 8000
_GPU_SETUP = (
    "vcf clair3 --gpu needs x86-64 Linux (or WSL2) with an NVIDIA GPU, Docker, "
    "the NVIDIA Container Toolkit and HKU's Clair3 GPU image: docker pull {image}"
)
WINDOWS_UNSUPPORTED = (
    "Clair3 is not available on Windows (Bioconda builds it for Linux and macOS "
    "only). Run `vcf clair3` on Linux, for example under WSL2."
)
# Before 2.0.5, libclair3 linked its own htslib, whose CRAM 3.1 decoder fails
# on valid blocks (#604) that the host samtools reads; 2.0.5 bundles htslib
# 1.24, which reads them.
CRAM31_NATIVE = (2, 0, 5)
_CRAM31_BUG = "Clair3's bundled htslib can fail to decode valid CRAM 3.1 blocks (#604)."
_ERROR_LINE = re.compile(r"^\[E::|\[ERROR\]|^Traceback|^[\w.]*(Error|Exception):")
# Clair3 calls these unless given a BED (CheckEnvs' major_contigs).
_MAJOR_CONTIGS = frozenset(
    f"{prefix}{name}"
    for prefix in ("chr", "")
    for name in (*map(str, range(1, 23)), "X", "Y")
)


def require_supported_platform() -> None:
    if sys.platform == "win32":
        raise WGSExtractError(WINDOWS_UNSUPPORTED)
    if not clair3_host_supported():
        raise WGSExtractError(
            "Clair3 runs on Linux (x86-64, ARM64) and Apple Silicon macOS only; "
            "Bioconda has no Clair3 2.x build for this host. Use `vcf deepvariant` "
            "(the ONT default) here."
        )


def clair3_gpu_image() -> str:
    return str(settings.get("clair3_gpu_image") or DEFAULT_GPU_IMAGE)


@dataclass(frozen=True)
class GpuSetup:
    """What `vcf clair3 --gpu` runs: the image, the Clair3 in it (None when
    unknown) and the GPUs (nvidia-smi indexes) with room for its workers."""

    image: str
    version: tuple[int, ...] | None
    devices: tuple[int, ...]


def _gpus_with_room() -> tuple[int, ...]:
    """The GPUs with the memory Clair3 starts a worker on. Clair3 reads every
    GPU it is given from nvidia-smi and fails on one that reports none (N/A),
    so it is given only these."""
    gpus = gpu_memory()
    need = GPU_FREE_MIB * 1024**2
    devices = tuple(
        gpu.index
        for gpu in gpus
        if gpu.free_bytes is not None and gpu.free_bytes >= need
    )
    if devices:
        return devices
    held = ", ".join(
        f"GPU {gpu.index}: "
        + (
            "free memory unknown (N/A)"
            if gpu.free_bytes is None
            else f"{gpu.free_bytes / 1024**3:.1f} GiB free"
        )
        for gpu in gpus
    )
    raise WGSExtractError(
        f"Clair3 on the GPU needs a GPU that nvidia-smi reports {GPU_FREE_MIB / 1024:.1f}"
        f" GiB free on ({held or 'nvidia-smi reported no GPU memory'}). Wait for "
        "other GPU programs to finish, or run without --gpu."
    )


def _require_cuda_torch(image: str) -> None:
    """Clair3 quietly runs on the CPU when its PyTorch can't use CUDA (a CPU
    build, or one too new for the driver), so check the image's first."""
    try:
        probe = run_container(
            image, ["python", "-c", _CUDA_PROBE], [],
            gpus=True, local_only=True, capture_output=True,
        )  # fmt: skip
        found = probe.stdout.split()[-1:]
    except (OSError, subprocess.SubprocessError) as error:
        stderr = getattr(error, "stderr", "") or ""
        detail = stderr.strip().splitlines()[-1:] or [str(error)]
        raise WGSExtractError(
            f"Couldn't check PyTorch's CUDA support in {image}: {detail[0]}"
        ) from error
    if found != ["True"]:
        raise WGSExtractError(
            f"PyTorch in {image} can't use the GPU (torch.cuda.is_available() is "
            "False), so Clair3 would run on the CPU. Use a Clair3 GPU image built "
            "for this NVIDIA driver (clair3_gpu_image in config.toml), or run "
            "without --gpu."
        )


def _image_clair3_version(image: str) -> tuple[int, ...] | None:
    """The Clair3 version in ``image``: the pinned default's is known, others
    are asked (their tags need not name it); None if they can't say."""
    if image == DEFAULT_GPU_IMAGE:
        return DEFAULT_GPU_VERSION
    try:
        result = run_container(
            image, [GPU_CLAIR3, "-v"], [], local_only=True, capture_output=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # NVIDIA's CUDA images print their CUDA version first.
    named = re.search(r"Clair3 v?(\d+\.\d+\.\d+)", result.stdout or "")
    return _parse_version(named[1]) if named else None


def verify_gpu() -> GpuSetup | MpsSetup:
    """What --gpu runs on, once this host can run Clair3 on a GPU with the
    memory it asks for; else name what's missing."""
    image = clair3_gpu_image()
    setup = _GPU_SETUP.format(image=image)
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in ("arm64", "aarch64"):
        return verify_mps()
    if not sys.platform.startswith("linux") or machine not in ("x86_64", "amd64"):
        raise UsageError(
            "vcf clair3 --gpu needs x86-64 Linux with an NVIDIA GPU, "
            "or Apple Silicon macOS with the WGSE-built MPS package. "
            "Run without --gpu here."
        )
    verify_gpu_docker(setup)
    require_image(
        image,
        f"The Clair3 GPU image isn't downloaded: docker pull {image} "
        "(set clair3_gpu_image in config.toml for another build).",
    )
    devices = _gpus_with_room()
    _require_cuda_torch(image)
    return GpuSetup(image, _image_clair3_version(image), devices)


def _parse_version(text: str | None) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(part) for part in match.groups()) if match else None


def clair3_version() -> tuple[int, ...] | None:
    """The installed Clair3's version (``Clair3 v2.0.5``), or None if unknown."""
    return _parse_version(get_tool_version(CLAIR3))


def needs_cram30_staging(version: tuple[int, ...] | None) -> bool:
    """Older (or unidentified) Clair3 builds get a CRAM 3.0 copy of CRAM 3.1."""
    return version is None or version < CRAM31_NATIVE


def sample_name(header: str, reads: str) -> str:
    """The reads' @RG SM (Clair3 would otherwise write "SAMPLE")."""
    samples = {group["SM"] for group in read_groups(header) if group.get("SM")}
    if len(samples) > 1:
        raise WGSExtractError(
            f"The read groups name several samples ({', '.join(sorted(samples))}); "
            "Clair3 calls one sample per run. Split the BAM/CRAM by sample first."
        )
    if samples:
        return samples.pop()
    name = Path(reads).name.split(".")[0]
    logging.warning(f"No @RG SM sample name in {reads}; naming the sample {name}.")
    return name


def require_ont_read_groups(header: str) -> None:
    """Reads from other platforms must not enter an ONT model."""
    platforms = {
        group["PL"].upper() for group in read_groups(header) if group.get("PL")
    }
    if platforms - {"ONT"}:
        raise WGSExtractError(
            f"The read groups name platform(s) {', '.join(sorted(platforms))}; "
            "`vcf clair3` calls Nanopore (PL:ONT) reads only. Use `vcf deepvariant` "
            "for other platforms, or split out the ONT read groups."
        )


def container_paths(paths: list[str]) -> list[str]:
    """``paths`` and their index files, with each link's chain to its target,
    so a container that mounts their folders opens them all."""
    found = []
    for path in paths:
        candidates = [path]
        candidates += [f"{path}{ext}" for ext in (*READS_INDEXES, *REFERENCE_INDEXES)]
        if Path(path).suffix.lower() in (".bam", ".cram"):
            candidates += [str(Path(path).with_suffix(ext)) for ext in READS_INDEXES]
        for candidate in candidates:
            if candidate == path or Path(candidate).exists():
                found += link_chain(candidate)
    return found


def space_free_run_paths(
    reads: str, ref: str, model: Path, work: str
) -> AbstractContextManager[list[str]]:
    """The reads, reference, model and work folder, as space-free links where
    they have spaces: Clair3's shell and GNU parallel steps split paths at
    spaces (#623). Clair3 reads the model's chemistry from its folder name,
    which the link keeps."""
    model_name = re.sub(r"\s", "_", model.name) or "model"
    return space_free_paths(
        "Clair3",
        [
            (reads, f"reads{''.join(Path(reads).suffixes[-1:])}", READS_INDEXES),
            (ref, "ref.fa", REFERENCE_INDEXES),
            (model, f"model/{model_name}", ()),
            (work, "work", ()),
        ],
    )


def clair3_errors(output: Path, limit: int = 8) -> str:
    """Distinct error lines from Clair3's logs: it reports them on stdout
    (tee'd to ``output/run_clair3.log`` and the step logs in ``output/log``),
    so they never reach the failed command's stderr."""
    seen: dict[str, None] = {}
    logs = [output / "run_clair3.log", *sorted(output.glob("log/*.log"))]
    for log in logs:
        if not log.is_file() or log.name.startswith("parallel_"):
            continue  # GNU parallel job tables.
        for line in log.read_text(errors="replace").splitlines():
            line = line.strip()
            if _ERROR_LINE.search(line):
                seen.setdefault(line, None)
    return "\n".join(list(seen)[:limit])


def _calls_contigs_with_reads(
    reads: str, ref: str, intervals: Intervals | None
) -> bool:
    """Whether a contig Clair3 calls (the region's, else chr1-22, X and Y)
    has mapped reads by the index, as Clair3's CheckEnvs asks; True when
    the index can't say."""
    try:
        with_reads = contigs_with_reads(reads, ref) if alignment_index(reads) else None
    except (OSError, ValueError, subprocess.SubprocessError, WGSExtractError) as e:
        logging.debug(f"Could not list the contigs with reads in {reads}: {e}")
        with_reads = None
    if with_reads is None:
        return True
    return bool(set(intervals or _MAJOR_CONTIGS) & set(with_reads))


def called_bases(reads: str, ref: str, intervals: Intervals | None) -> int:
    """Reference bases Clair3 calls: the region's, else chr1-22, X and Y
    (its default), on the contigs the index places reads on (all of them
    when the index can't say)."""
    try:
        with_reads = contigs_with_reads(reads, ref)
    except (OSError, ValueError, subprocess.SubprocessError, WGSExtractError) as e:
        logging.debug(f"Could not list the contigs with reads in {reads}: {e}")
        with_reads = {}
    if intervals:
        return sum(
            end - start
            for contig, ranges in intervals.items()
            if not with_reads or contig in with_reads
            for start, end in ranges
        )
    lengths = with_reads or read_fai_lengths(ref)
    return sum(n for contig, n in lengths.items() if contig in _MAJOR_CONTIGS)


def mean_depth(reads: str, ref: str, region: str | None) -> float | None:
    """The reads' sampled mean depth: over ``region`` (its shallowest
    chromosome group, such as a man's chrX), else over the bases they cover,
    autosomes first. None when it can't be sampled."""
    try:
        if region:
            found = covered_depths(
                reads, region_targets(region), region=region, ref=ref
            )
            if depth := min((d for d in found.values() if d > 0), default=0.0):
                return depth
        depths = covered_depths(reads, (AUTOSOMES, CHR_X, CHR_Y), ref=ref)
    except (OSError, ValueError, WGSExtractError) as error:
        logging.debug(f"Could not sample the depth of {reads}: {error}")
        return None
    return depths.get(AUTOSOMES) or max(depths.values(), default=0.0) or None


@dataclass(frozen=True)
class TempSpace:
    """What Clair3's work folder holds at its peak: ``bases`` called at
    ``depth`` (None when unknown), on the GPU or not."""

    bases: int
    depth: float | None
    gpu: bool
    gvcf: bool = False

    def check(
        self,
        outdir: str,
        threads: str,
        *,
        enforce: bool = True,
        copy: int = 0,
        recheck: bool = False,
    ) -> None:
        """Report the peak in temporary work, and the ``copy`` of the reads
        to write there, with Clair3's host requirement, and refuse a run
        that wouldn't fit (#811, #690): it fails hours in otherwise. A
        ``recheck`` (once that copy is written) says nothing more unless the
        disk is now short."""
        estimate = estimate_clair3(
            outdir, self.bases, gpu=self.gpu, depth=self.depth, gvcf=self.gvcf
        )
        (disk,) = estimate.resources
        caller = "clair3-gpu" if self.gpu else "clair3"
        requirement = HostRequirement(
            "Clair3",
            (
                (
                    outdir,
                    None
                    if disk.estimated_bytes is None
                    else disk.estimated_bytes + copy,
                ),
            ),
            None if recheck else CLAIR3_MEMORY,
            int(threads),
            calling_hours(caller, self.bases, int(threads))
            * (CLAIR3_GVCF_TIME_FACTOR[caller] if self.gvcf else 1),
        )
        if recheck and not any(v.short for v in requirement.volumes()):
            return
        report_resources(estimate)
        check_host_requirement(
            requirement,
            enforce=enforce,
            disk_advice="Free up space, choose a --tmp-dir on a bigger disk or "
            "call a smaller --region.",
            memory_advice="Lower --threads.",
        )


def temp_space(
    reads: str,
    ref: str,
    intervals: Intervals | None,
    region: str | None,
    gpu: bool,
    gvcf: bool = False,
) -> TempSpace | None:
    """Clair3's temporary space for ``reads``, or None when its called bases
    can't be counted."""
    try:
        bases = called_bases(reads, ref, intervals)
    except (OSError, ValueError) as e:
        logging.warning(f"Clair3's temporary space wasn't estimated: {e}")
        return None
    return TempSpace(bases, mean_depth(reads, ref, region) if gpu else None, gpu, gvcf)


def run_clair3(params: VcfClair3Params) -> None:
    from wgsextract_cli.core.reference_evidence import CLAIR3_MIN_MAPPING_QUALITY

    from .caller_ploidy import GvcfEngine, call_partitioned

    require_supported_platform()
    accelerator = verify_gpu() if params.gpu else None
    gpu = accelerator if isinstance(accelerator, GpuSetup) else None
    mps = accelerator if isinstance(accelerator, MpsSetup) else None
    if gpu:
        verify_dependencies(["samtools", "bcftools", "tabix"])
        logging.info(
            f"Running Clair3 on GPU {', '.join(map(str, gpu.devices))} from "
            f"{image_record(gpu.image)['image']}."
        )
    else:
        caller = "wgse-clair3" if mps else CLAIR3
        verify_dependencies([caller, "samtools", "bcftools", "tabix"])
        log_dependency_info([caller])
        if mps:
            logging.info(
                f"Running Clair3 {mps.version}: MPS inference, CPU streaming/scaling."
            )
    evidence = None
    if params.reference_evidence == "gvcf":
        version = (
            str(image_record(gpu.image)["image"])
            if gpu
            else mps.version
            if mps
            else ".".join(map(str, clair3_version() or ())) or "unknown"
        )
        evidence = GvcfEngine(
            "clair3", version, min_mapping_quality=CLAIR3_MIN_MAPPING_QUALITY
        )
        factor = CLAIR3_GVCF_TIME_FACTOR["clair3-gpu" if gpu else "clair3"]
        warn(
            "clair3_gvcf_cost",
            "Clair3 writes its gVCF from a slower pileup: expect about "
            f"{factor:g}x the calling time (--reference-evidence none skips it)."
            + (
                " In one 5 Mb measurement (HG002 ONT chr20:10-15 Mb, 4 threads, "
                "RTX 3090) the CPU was faster with the gVCF (179 s against "
                "452 s); larger runs were not measured."
                if gpu
                else ""
            ),
            time_factor=factor,
            gpu=bool(gpu),
        )
    call_partitioned(
        params,
        setup_caller(params),
        "clair3",
        partial(_run_clair3, gpu=gpu, mps=mps),
        evidence,
        gpu_ploidy=mps is not None,
    )


def _run_clair3(
    params: VcfClair3Params,
    sample_ploidy: int = 2,
    setup: CallerSetup | None = None,
    record: bool = True,
    gvcf: str | None = None,
    *,
    gpu: GpuSetup | None = None,
    mps: MpsSetup | None = None,
) -> None:
    """``vcf clair3``: Clair3 on Nanopore reads into ``clair3.vcf.gz``, and
    with ``gvcf`` its gVCF there."""
    setup = setup or setup_caller(params)
    threads, outdir, ref = setup.threads, setup.outdir, setup.ref
    input_file, region = str(params.input), params.region
    detected = setup.platform
    if detected not in (None, "ont"):
        raise WGSExtractError(
            f"The input looks like {detected} reads; `vcf clair3` calls Nanopore "
            "reads. Use `vcf deepvariant` for other platforms."
        )
    header = get_bam_header(input_file, ref)
    require_ont_read_groups(header)
    sample = sample_name(header, input_file)
    model = select_model(params.model, header, mps.launcher if mps else None)

    out_vcf = str(Path(outdir, "clair3.vcf.gz"))
    logging.info(LOG_MESSAGES["vcf_calling_clair3"].format(output=out_vcf))
    with (
        plain_reference(ref, outdir) as use_ref,
        scratch_directory(outdir, ".wgse_clair3_") as work,
    ):
        bed, intervals = None, None
        if region:
            lengths = read_fai_lengths(use_ref)
            bed = str(Path(work, "regions.bed"))
            write_region_bed(region, lengths, bed)
            try:
                intervals = selected_intervals(region, lengths, commas=True)
            except (ValueError, OSError) as error:
                raise WGSExtractError(
                    f"Invalid Clair3 region selection: {region}: {error}"
                ) from error
        space = temp_space(
            input_file, use_ref, intervals, region, gpu is not None, gvcf is not None
        )
        stage = needs_cram30_staging(
            gpu.version if gpu else mps.upstream_version if mps else clair3_version()
        )
        if space:
            # A CRAM 3.0 copy stays beside the tensors while Clair3 runs.
            copy = (
                staging_bytes(input_file, use_ref, intervals)
                if stage and needs_cram30(input_file)
                else 0
            )
            space.check(work, threads, enforce=not params.no_preflight, copy=copy)
        reads = input_file
        if stage:
            staging = Path(work, "reads")
            staging.mkdir()
            reads = stage_cram30(
                input_file, use_ref, str(staging), threads, intervals,
                tool="Clair3", why=_CRAM31_BUG,
            )  # fmt: skip
            if space and reads != input_file:
                # Its estimate was a guess; the copy is now on the disk.
                space.check(
                    work, threads, enforce=not params.no_preflight, recheck=True
                )
        output = str(Path(work, "clair3"))
        # Absolute, so the header's paths can be told apart and restored.
        abs_reads, abs_ref, abs_work = (
            str(absolute_path(p)) for p in (reads, use_ref, work)
        )
        with space_free_run_paths(abs_reads, abs_ref, model, abs_work) as links:
            run_reads, run_ref, run_model, run_work = links
            command = clair3_command(
                run_reads, run_ref, Path(run_model), threads,
                str(Path(run_work, "clair3")),
                str(Path(run_work, "regions.bed")) if bed else None, sample,
                gpu.devices if gpu else (),
                mps=mps is not None,
            )  # fmt: skip
            if sample_ploidy == 1:
                command.append("--haploid_precise")
            if gvcf:
                command.append("--gvcf")
            try:
                if gpu:
                    weights = [str(Path(run_model, name)) for name in MODEL_FILES]
                    # Clair3 starts GPU workers by free GPU memory, not
                    # --threads; the CPU cap keeps it to the thread budget.
                    run_container(
                        gpu.image, command,
                        container_paths([run_reads, run_ref, run_model, *weights,
                                         run_work]),
                        gpus=True, cpus=int(threads), local_only=True,
                        temporary_directory=run_work,
                    )  # fmt: skip
                else:
                    # stdin closed: Clair3's GNU parallel would read it otherwise.
                    run_command(command, env=clair3_env(), stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError) as e:
                errors = clair3_errors(Path(output))
                if not errors or not isinstance(e, subprocess.CalledProcessError):
                    raise WGSExtractError(f"Clair3 failed: {e}") from e
                stderr = (
                    e.stderr.decode(errors="replace")
                    if isinstance(e.stderr, bytes)
                    else (e.stderr or "")
                )
                # Clair3's stderr (when any) as run_command reports it, then its logs.
                reported = f"{e}\n" if stderr.strip() else f"exit {e.returncode}; "
                raise WGSExtractError(
                    f"Clair3 failed: {reported}Clair3's log errors:\n{errors}"
                ) from e
        merged = Path(output, "merge_output.vcf.gz")
        if not merged.is_file():
            raise WGSExtractError("Clair3 failed to produce merge_output.vcf.gz.")
        # Name the user's files, not the deleted links, in the header.
        real_paths = {
            run_reads: input_file, run_ref: ref, run_model: str(model),
            run_work: work,
        }  # fmt: skip
        named = restore_vcf_paths(str(merged), real_paths, work)
        with safe_output(out_vcf) as staged:
            run_command(["bcftools", "view", "-Oz", "-o", staged, named])
        if gvcf:
            evidence = Path(output, "merge_output.gvcf.gz")
            if evidence.is_file():
                named = restore_vcf_paths(str(evidence), real_paths, work)
                run_command(["bcftools", "view", "-Oz", "-o", gvcf, named])
            elif not _calls_contigs_with_reads(input_file, use_ref, intervals):
                # Clair3 skips contigs without reads, and with none left writes
                # a header-only VCF and no gVCF: its bases read as uncovered.
                run_command(["bcftools", "view", "-h", "-Oz", "-o", gvcf, out_vcf])
            else:
                raise WGSExtractError("Clair3 failed to produce merge_output.gvcf.gz.")
    ensure_vcf_indexed(out_vcf)
    if record:
        record_variants(out_vcf, "variants", warn_if_no_variants(out_vcf, ref, region))


cmd_clair3 = cli_handler(VcfClair3Params, run_clair3)
