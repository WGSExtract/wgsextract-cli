import contextlib
import glob
import hashlib
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.vcf import DEEPVARIANT_STEPS, VcfDeepvariantParams
from wgsextract_cli.core import alignment_memory, native_deepvariant, ultima
from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.containers import (
    docker,
    docker_info,
    host_path,
    image_record,
    require_image,
    run_container,
)
from wgsextract_cli.core.cram_staging import (
    needs_cram30,
    region_intervals,
    stage_cram30,
)
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.native_support import is_arm64_docker_host, require_native_tool
from wgsextract_cli.core.output_checks import warn_if_no_variants
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.reference_assets import read_fai_lengths
from wgsextract_cli.core.region_selection import (
    region_items,
)
from wgsextract_cli.core.resource_estimates import (
    GIB,
    PostprocessPlan,
    dv_calling_regions,
    estimate_resources,
    plan_dv_postprocess,
    preflight_resources,
)
from wgsextract_cli.core.results import warn
from wgsextract_cli.core.sequencing import input_platform, refuse_mixed_platforms
from wgsextract_cli.core.steps import Decision, Step, StepJournal, resume_policy
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command
from wgsextract_cli.core.variant_files import ensure_vcf_indexed

from .basic import CallerSetup, record_variants, setup_caller

# Google's image; Bioconda's deepvariant has shipped no DeepVariant since 1.8.
DEFAULT_IMAGE = "google/deepvariant:1.10.0"
DEFAULT_ARM64_IMAGE = "localhost/wgsextract-deepvariant:1.10.0-arm64"
# Google's experimental image with the ULTIMA model (google/deepvariant#1100),
# until a release has it.
DEFAULT_ULTIMA_IMAGE = "google/deepvariant:gh1100-ultima"
# Its GPU build, for --gpu (Parabricks has no ULTIMA model), pinned to the
# build validated in #358.
DEFAULT_ULTIMA_GPU_IMAGE = (
    "google/deepvariant:gh1100-ultima-gpu"
    "@sha256:f5572ef9b996c619c9e73a3f3391f894af72bf868d27578d9a4985298e75a1f7"
)
# Gate/remove this workaround when supported DV images ship htslib >=1.19
# (htscodecs >=1.5.2); a host-samtools decode cannot detect their SSE4 bug.
_CRAM31_BUG = (
    "DeepVariant's x86-64 images (htslib 1.18 / htscodecs 1.5.1) have an SSE4 "
    "rANS decoder bug that can fail on valid CRAM 3.1 blocks; staging applies "
    "to every image."
)
_SETUP = (
    "DeepVariant runs from Google's Docker image on x86-64 Linux and Intel "
    "Macs: install Docker, then docker pull {image}. "
    "Or put Google's run_deepvariant on PATH."
)


def deepvariant_image(model_type: str = "WGS", gpu: bool = False) -> str:
    if model_type == "ULTIMA" and gpu:
        return str(
            settings.get("deepvariant_ultima_gpu_image") or DEFAULT_ULTIMA_GPU_IMAGE
        )
    if model_type == "ULTIMA":
        return str(settings.get("deepvariant_ultima_image") or DEFAULT_ULTIMA_IMAGE)
    default = DEFAULT_ARM64_IMAGE if is_arm64_docker_host() else DEFAULT_IMAGE
    return str(settings.get("deepvariant_image") or default)


_ARM64_SETUP = (
    "Build the native CPU DeepVariant 1.10.0 image on Linux ARM64 with "
    "packaging/deepvariant-arm64/build.sh (on a Mac, build it there and move "
    "it with docker save / docker load; see docs/deepvariant-arm64.md); "
    "set deepvariant_image in config.toml if you "
    "use another local tag. Dependencies and images must already be installed. "
    "Google's x86 image, emulation and unaudited PATH launchers are not supported."
)
_ARM64_ALTERNATIVES = (
    "Otherwise call variants with vcf clair3 (ONT reads), vcf snp and vcf indel "
    "(bcftools, short reads), or run DeepVariant on an x86-64 host."
)


def _require_arm64_wgs(model_type: str, checkpoint: str | None = None) -> None:
    if model_type != "WGS" or checkpoint:
        selection = "--checkpoint" if checkpoint else f"model {model_type}"
        raise WGSExtractError(
            f"DeepVariant {selection} is not qualified on ARM64; "
            f"only the default WGS model is qualified. {_ARM64_ALTERNATIVES}"
        )


def _verify_arm64_image(model_type: str = "WGS", checkpoint: str | None = None) -> str:
    _require_arm64_wgs(model_type, checkpoint)
    docker_info(_ARM64_SETUP)
    architecture = docker_info(_ARM64_SETUP, "{{.Architecture}}").strip().lower()
    if architecture not in {"aarch64", "arm64"}:
        raise WGSExtractError(
            "DeepVariant requires a native ARM64 Docker engine on ARM64; "
            "an x64 engine or emulation is not supported."
        )
    image = deepvariant_image(model_type)
    require_image(
        image,
        f"Native DeepVariant image isn't installed: {image}. {_ARM64_SETUP} "
        f"{_ARM64_ALTERNATIVES}",
    )
    found = _image_platform(image, _ARM64_SETUP)
    _refuse_emulated_amd64(image, architecture, found)
    if found != "linux/arm64":
        raise WGSExtractError(
            f"DeepVariant image {image} must be linux/arm64; "
            "x86 images and emulation are not supported."
        )
    return image


def _image_platform(image: str, setup: str) -> str:
    """The image's ``os/architecture`` (``linux/amd64``), or "" if unknown."""
    inspected = run_command(
        [docker(setup), "image", "inspect", "--format",
         "{{.Os}}/{{.Architecture}}", image],
        capture_output=True,
        check=False,
    )  # fmt: skip
    return (inspected.stdout or "").strip() if not inspected.returncode else ""


def _refuse_emulated_amd64(image: str, engine: str, found: str) -> None:
    """An amd64 image on an ARM64 engine aborts in its first stage (#665)."""
    if engine in {"aarch64", "arm64"} and found == "linux/amd64":
        raise WGSExtractError(
            f"DeepVariant image {image} is linux/amd64 and can't run on this "
            "ARM64 Docker engine: Google publishes DeepVariant for x86-64 only, "
            "and Docker's x86-64 emulation (including Docker Desktop on Apple "
            "Silicon) lacks the AVX instructions its TensorFlow needs, so it "
            f"aborts (exit 134). {_ARM64_SETUP} {_ARM64_ALTERNATIVES}"
        )


def _model_type(
    params: VcfDeepvariantParams,
    ref: str | None = None,
    platform: str | None = None,
) -> str:
    """Explicit model selectors take precedence over platform defaults: the
    detected ``platform``, else (given ``ref``) the reads' own."""
    model, ont = params.model_type, params.ont
    if (ont or model == "ONT_R104") and (
        params.pacbio or params.wes or (ont and model not in (None, "ONT_R104"))
    ):
        raise UsageError(
            "--ont/--model-type ONT_R104 cannot be combined with "
            "--pacbio, --wes or a different --model-type."
        )
    if ont:
        return "ONT_R104"
    if params.pacbio:
        return "PACBIO"
    if model:
        return str(model)
    if params.wes:
        return "WES"
    input_file = str(params.input)
    detected = platform
    if detected is None and ref:
        detected = input_platform(input_file, ref)
        if detected is None:
            refuse_mixed_platforms(input_file, ref, "--model-type (or --ont/--pacbio)")
    if detected == "ont":
        logging.info(
            "ONT reads: calling with DeepVariant's ONT_R104 model (R10.4.1 only)."
        )
        return "ONT_R104"
    if ref and ultima.alignment_flow_data(input_file, ref).flow_aware:
        logging.info("Ultima reads: calling with DeepVariant's ULTIMA model.")
        return "ULTIMA"
    return "WGS"


def _require_ont_x86(model_type: str) -> None:
    if model_type == "ONT_R104" and platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        raise WGSExtractError(
            "DeepVariant ONT_R104 requires x86-64; it is not qualified on ARM64, "
            "including Apple Silicon. Use vcf clair3, or an x86-64 host."
        )


def _warn_ont_chemistry(alignment: str, ref: str) -> None:
    metadata = []
    for line in get_bam_header(alignment, ref).splitlines():
        if line.startswith("@PG\t"):
            metadata.append(line)
        elif line.startswith("@RG\t"):
            metadata.extend(
                field for field in line.split("\t") if field.startswith("DS:")
            )
    if re.search(
        r"(?<![a-z0-9])r9[._-]4(?:[._-]1)?(?![a-z0-9])",
        "\n".join(metadata),
        re.IGNORECASE,
    ):
        logging.warning(
            "R9.4.1/R9.4 basecaller metadata detected: this chemistry is unsupported "
            "by DeepVariant ONT_R104, which supports R10.4.1 only. "
            "Do not interpret these calls as validated ONT results."
        )


def _verify_docker(model_type: str = "WGS") -> None:
    """Docker runs here (Windows can't run DeepVariant's Linux image)."""
    setup = _SETUP.format(image=deepvariant_image(model_type))
    if sys.platform == "win32":
        raise WGSExtractError(f"DeepVariant isn't available on Windows. {setup}")
    docker_info(setup)


def _verify_docker_image(model_type: str, gpu: bool = False) -> str:
    image = deepvariant_image(model_type, gpu)
    setting = (
        "deepvariant_ultima_gpu_image"
        if gpu
        else "deepvariant_ultima_image"
        if model_type == "ULTIMA"
        else "deepvariant_image"
    )
    require_image(
        image,
        f"The DeepVariant image isn't downloaded: docker pull {image} "
        f"(set {setting} in config.toml for another version).",
    )
    setup = _SETUP.format(image=image)
    engine = docker_info(setup, "{{.Architecture}}").strip().lower()
    _refuse_emulated_amd64(image, engine, _image_platform(image, setup))
    return image


def _dv_env() -> dict[str, str]:
    # DeepVariant runs its own Python; don't leak ours into it.
    env = dict(os.environ)
    for var in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(var, None)
    return env


def _container_regions(region: str) -> str:
    """``region`` with its BED files at the paths mounted in the container."""
    return " ".join(
        host_path(p) if Path(p).is_file() else p
        for p in region_items(region, commas=False)
    )


def _available_memory(setup: str | None) -> int | None:
    """Free host RAM, capped by the Docker engine's (e.g. Docker Desktop's VM)."""
    try:
        available = alignment_memory.available_memory()
    except OSError:
        return None
    if setup is not None:
        engine = docker_info(setup, "{{.MemTotal}}").strip()
        if engine.isdigit() and int(engine) > 0:
            available = min(available, int(engine))
    return available


def _supports_num_partitions(
    image: str | None, run_deepvariant: str | None, setup: str | None
) -> bool:
    """Whether postprocess_variants has --num_partitions (DeepVariant 1.9+).

    Pinned defaults and version tags decide without running anything;
    otherwise ask the image's or launcher's postprocess_variants.
    """
    if image in {
        DEFAULT_IMAGE,
        DEFAULT_ARM64_IMAGE,
        DEFAULT_ULTIMA_IMAGE,
        DEFAULT_ULTIMA_GPU_IMAGE,
    }:
        return True
    if image:
        tag = re.search(r":v?(\d+)\.(\d+)[^/:@]*(?:@|$)", image)
        if tag:
            return (int(tag.group(1)), int(tag.group(2))) >= (1, 9)
        probe = [
            docker(setup or ""), "run", "--rm", "--pull=never",
            "--entrypoint", "/opt/deepvariant/bin/postprocess_variants",
            image, "--helpfull",
        ]  # fmt: skip
    elif run_deepvariant:
        tool = Path(run_deepvariant).with_name("postprocess_variants")
        if not tool.is_file():
            return False
        probe = [str(tool), "--helpfull"]
    else:
        return False
    try:
        result = run_command(probe, capture_output=True, check=False, env=_dv_env())
    except OSError:
        return False
    return "num_partitions" in (result.stdout or "") + (result.stderr or "")


def _postprocess_plan(
    model_type: str,
    ref: str,
    region: str | None,
    threads: int,
    available: int | None,
    supports_partitions: Callable[[], bool],
) -> PostprocessPlan | None:
    """Bound postprocess_variants by RAM; raise when one worker cannot fit.

    ``supports_partitions`` is asked only when more partitions are wanted.
    """
    if model_type == "WES":
        return None  # run_deepvariant postprocesses WES sequentially.
    lengths = read_fai_lengths(ref)
    if not lengths:
        logging.warning(
            f"No {ref}.fai: DeepVariant's postprocess_variants RAM isn't bounded."
        )
        return None
    if region:
        region_intervals(
            region, lengths, "DeepVariant"
        )  # Report bad selections clearly.
    regions = dv_calling_regions(lengths, region)
    assert regions is not None
    plan = plan_dv_postprocess(regions, threads, available)
    upgrade = ""
    if plan.partitions > plan.cpus and not supports_partitions():
        upgrade = ", use DeepVariant 1.9+ (which can split this step further)"
        logging.warning(
            "This DeepVariant's postprocess_variants has no --num_partitions "
            "(DeepVariant 1.9+), so only its workers can be reduced to save memory."
        )
        plan = plan_dv_postprocess(regions, threads, available, partitions=False)
    if plan.fits is False and available is not None:
        raise WGSExtractError(
            "Not enough memory for DeepVariant's postprocess_variants step: one "
            f"worker sorting {plan.partitions} partitions needs ~"
            f"{plan.worker_bytes / GIB:.1f} GiB plus 1 GiB of headroom, but "
            f"{available / GIB:.1f} GiB is available. Free memory (close other "
            f"jobs), use a machine with more RAM{upgrade}, or call a smaller "
            "--region."
        )
    if plan.cpus < threads:
        logging.warning(
            f"Limiting DeepVariant postprocess_variants to {plan.cpus} of {threads} "
            f"workers over {plan.partitions} partitions (~"
            f"{plan.worker_bytes / GIB:.1f} GiB each) to fit "
            f"{(available or 0) / GIB:.1f} GiB of available memory."
        )
    elif plan.partitions > threads:
        logging.info(
            f"Splitting DeepVariant postprocess_variants into {plan.partitions} "
            f"partitions (~{plan.worker_bytes / GIB:.1f} GiB per worker) to fit "
            f"{(available or 0) / GIB:.1f} GiB of available memory."
        )
    return plan


def _postprocess_args(plan: PostprocessPlan | None, threads: int) -> list[str]:
    if plan is None:
        return []
    flags = []
    if plan.cpus != threads:
        flags += ["--postprocess_cpus", str(plan.cpus)]
    if plan.partitions > plan.cpus:
        # postprocess_variants' --num_partitions needs DeepVariant 1.9+.
        flags += [
            "--postprocess_variants_extra_args",
            f"num_partitions={plan.partitions}",
        ]
    return flags


_OOM_ERROR = (
    "DeepVariant ran out of memory: a process in its container was OOM-killed, "
    "so the run was stopped rather than left waiting on the killed worker. "
    "Free memory (close other jobs) or lower --threads, then rerun."
)


# Parabricks DeepVariant: its --mode per model type (WGS and WES are short reads).
_PARABRICKS_MODES = {
    "WGS": [],
    "WES": ["--use-wes-model"],
    "PACBIO": ["--mode", "pacbio"],
    "ONT_R104": ["--mode", "ont"],
}


_GPU_REFUSAL = (
    "--gpu runs DeepVariant's WGS, WES, PACBIO and ONT_R104 models with "
    "Parabricks, and ULTIMA with Google's DeepVariant GPU image; use the CPU "
    "version for --checkpoint or HYBRID_PACBIO_ILLUMINA."
)
_ULTIMA_GPU_SETUP = (
    "DeepVariant's ULTIMA model runs on the GPU in Google's experimental image, "
    "on x86-64 Linux (or WSL2) with an NVIDIA GPU, Docker and the NVIDIA "
    "Container Toolkit: docker pull {image}"
)
# Free GPU memory call_variants needs in Google's GPU image (#358). On a 24 GiB
# RTX 3090 with another process holding the rest, the ULTIMA model ran with
# 7.3 GiB free and ran out of memory with 6.3 GiB.
ULTIMA_GPU_MIN_FREE = round(7.3 * GIB)
# call_variants' default --batch_size, which that measurement used.
CALL_VARIANTS_BATCH = 1024


def _model_chosen(params: VcfDeepvariantParams) -> bool:
    """Whether the options, not the reads, choose the model."""
    return bool(params.model_type or params.ont or params.pacbio or params.wes)


def _gpu_model(params: VcfDeepvariantParams) -> tuple[str, CallerSetup | None]:
    """The model --gpu calls with, and the caller setup made to detect it from
    the reads when the options don't choose it."""
    model = _model_type(params)
    if params.checkpoint or (model not in _PARABRICKS_MODES and model != "ULTIMA"):
        raise UsageError(_GPU_REFUSAL)
    if _model_chosen(params):
        return model, None
    caller = setup_caller(params)
    return _model_type(params, caller.ref, caller.platform), caller


def require_gpu_host() -> None:
    """Refuse --gpu where neither Parabricks nor Google's ULTIMA GPU image
    runs: both are x86-64 Linux only."""
    if not sys.platform.startswith("linux"):
        raise UsageError(
            "--gpu is not supported on this platform; use Linux with an NVIDIA GPU."
        )
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise UsageError(
            "--gpu is not supported on Linux ARM64: Parabricks and Google's "
            "ULTIMA GPU image are x86-64 only. Use the native CPU pipeline instead."
        )


def _verify_ultima_gpu(params: VcfDeepvariantParams) -> None:
    """Refuse an Ultima --gpu run this machine can't do, before any work."""
    from wgsextract_cli.core.parabricks import verify_gpu_docker

    setup = _ULTIMA_GPU_SETUP.format(image=deepvariant_image("ULTIMA", gpu=True))
    if not params.dry_run:
        # A dry run only plans, in the image, without the GPU.
        verify_gpu_docker(setup)
    else:
        docker_info(setup)


def _check_gpu_free(check: bool, batch_size: int | None) -> None:
    """Refuse (unless not ``check``) when a GPU lacks the memory call_variants
    needs; just before it runs, so plans and finished reruns don't need one.
    Every GPU is checked: the container gets them all."""
    from wgsextract_cli.core.parabricks import gpu_memory

    gpus = gpu_memory()
    free = [gpu.free_bytes for gpu in gpus if gpu.free_bytes is not None]
    if not free:
        logging.warning("GPU memory is unknown; running call_variants anyway.")
        return
    if check and batch_size not in (None, CALL_VARIANTS_BATCH):
        warn(
            "deepvariant_gpu_batch_size",
            f"--batch-size {batch_size}: the {ULTIMA_GPU_MIN_FREE / GIB:.1f} GiB "
            "free-GPU-memory check assumes call_variants' default batch of "
            f"{CALL_VARIANTS_BATCH}, so it may pass a batch that runs out of GPU "
            "memory or refuse one that fits.",
        )
    least = min(free)
    if least >= ULTIMA_GPU_MIN_FREE:
        return
    message = (
        f"DeepVariant call_variants needs ~{ULTIMA_GPU_MIN_FREE / GIB:.1f} GiB of "
        f"free memory on each GPU, but one has {least / GIB:.1f} GiB free "
        "(other programs are using it)."
    )
    if check:
        raise WGSExtractError(
            f"{message} Wait for them to finish, or pass --no-gpu-memory-check "
            "to try anyway."
        )
    warn("deepvariant_gpu_memory", f"{message} Continuing (--no-gpu-memory-check).")


def _deepvariant_parabricks(
    params: VcfDeepvariantParams, model_type: str, caller: CallerSetup | None
) -> None:
    from wgsextract_cli.core.parabricks import (
        call_variants,
        plan_gpu_memory,
        verify_parabricks,
    )

    if params.batch_size is not None:
        raise UsageError("DeepVariant --batch-size doesn't apply to Parabricks.")
    if params.dry_run or params.force_steps:
        raise UsageError(
            "--dry-run and --force-step apply to DeepVariant's stages; "
            "Parabricks (--gpu) always runs in one step."
        )
    require_native_tool("parabricks")
    verify_dependencies(["samtools", "tabix"])
    verify_parabricks()
    fit = plan_gpu_memory(
        "deepvariant", "deepvariant", check=not params.no_gpu_memory_check
    )
    setup = caller or setup_caller(params)
    outdir, ref = setup.outdir, setup.ref
    input_file, region = str(params.input), params.region
    _require_ont_x86(model_type)
    if model_type == "ONT_R104":
        _warn_ont_chemistry(input_file, ref)
    out_vcf = str(Path(outdir, "deepvariant.vcf.gz"))
    logging.info(LOG_MESSAGES["vcf_calling_deepvariant"].format(output=out_vcf))
    call_variants(
        "deepvariant", input_file, ref, out_vcf, region,
        _PARABRICKS_MODES[model_type] + fit,
    )  # fmt: skip
    ensure_vcf_indexed(out_vcf)
    record_variants(out_vcf, "variants", warn_if_no_variants(out_vcf, ref, region))


def _require_native_wgs(model_type: str, checkpoint: str | None) -> None:
    if model_type != "WGS" or checkpoint:
        selection = "--checkpoint" if checkpoint else f"model {model_type}"
        raise WGSExtractError(
            f"wgse-deepvariant runs only the WGS model; DeepVariant {selection} "
            f"isn't qualified on Apple Silicon. {_ARM64_ALTERNATIVES}"
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
) -> list[str]:
    """run_deepvariant's options, with the paths an image sees."""
    args = [
        "--model_type", model_type,
        "--ref", host_path(ref),
        "--reads", host_path(reads),
        "--output_vcf", host_path(vcf),
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
) -> list[str]:
    """wgse-deepvariant run's options. A selection goes to it as one BED of
    the intervals it covers, in reference order (BED files, literals or
    both, of any size: a command line holds at most 1 MiB on macOS), named
    by its content so a changed selection reruns the stages."""
    args = [
        "run",
        "--model_type=WGS",
        f"--ref={ref}",
        f"--reads={reads}",
        f"--output_vcf={vcf}",
        f"--num_shards={shards}",
        f"--intermediate_results_dir={work}",
        f"--checkpoint={native.checkpoint}",
        f"--small_model_path={native.small_model}",
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
    return args


def run_deepvariant(params: VcfDeepvariantParams) -> None:
    """``vcf deepvariant``: DeepVariant into ``deepvariant.vcf.gz``, in
    resumable stages, or Parabricks."""
    require_native_tool("run_deepvariant")
    batch_size = params.batch_size
    if batch_size is not None and batch_size < 1:
        raise UsageError("DeepVariant --batch-size must be positive.")
    caller = None
    gpu = params.gpu
    if gpu:
        require_gpu_host()
        if params.region and any(
            len(item.split()) > 1 for item in region_items(params.region, commas=True)
        ):
            # A spaced item would be one path to serve's --allow check but
            # several to the depth check and DeepVariant.
            raise UsageError(
                "--gpu regions are separated by commas, without spaces: "
                f"{params.region!r}"
            )
        model, caller = _gpu_model(params)
        if model != "ULTIMA":
            _deepvariant_parabricks(params, model, caller)
            return
        # Parabricks has no ULTIMA model: Google's GPU image runs DeepVariant's
        # stages, as on the CPU, with call_variants on the GPU.
        logging.info(
            "Ultima reads on the GPU: running DeepVariant's stages in Google's "
            "experimental GPU image (Parabricks has no ULTIMA model)."
        )
        _verify_ultima_gpu(params)
        if params.region:
            # --gpu's regions are comma-separated; DeepVariant's, spaced.
            params = replace(
                params, region=" ".join(region_items(params.region, commas=True))
            )
    checkpoint = str(params.checkpoint) if params.checkpoint else None
    native_path = None if gpu else native_deepvariant.installed()
    native = None
    if native_path:
        _require_native_wgs(_model_type(params), checkpoint)
        native = native_deepvariant.require(native_path)
    arm64_image = (
        _verify_arm64_image(_model_type(params), checkpoint)
        if is_arm64_docker_host() and not gpu and not native
        else None
    )
    if not arm64_image:
        _require_ont_x86(_model_type(params))
    local_dv = (
        native.path
        if native
        else None
        if arm64_image or gpu
        else get_tool_path("run_deepvariant")
    )
    if not local_dv and not arm64_image and not gpu:
        _verify_docker()
    verify_dependencies(["samtools", "tabix"])
    caller = caller or setup_caller(params)
    threads, outdir, ref = caller.threads, caller.outdir, caller.ref
    input_file, region = str(params.input), params.region
    model_type = _model_type(params, ref, caller.platform)
    if arm64_image:
        _require_arm64_wgs(model_type, checkpoint)
    if native:
        _require_native_wgs(model_type, checkpoint)
    _require_ont_x86(model_type)
    if model_type == "ONT_R104":
        _warn_ont_chemistry(input_file, ref)
    if model_type == "ULTIMA" and local_dv:
        # A local run_deepvariant is a release, which has no ULTIMA model yet.
        _verify_docker(model_type)
        local_dv = None
    image = arm64_image or (None if local_dv else _verify_docker_image(model_type, gpu))
    setup = (
        _ARM64_SETUP
        if arm64_image
        else (_ULTIMA_GPU_SETUP if gpu else _SETUP).format(image=image)
        if image
        else None
    )
    available = _available_memory(setup)
    # wgse-deepvariant's postprocess_variants is one process (estimate_resources).
    postprocess = (
        None
        if native
        else _postprocess_plan(
            model_type, ref, region, int(threads), available,
            lambda: _supports_num_partitions(image, local_dv, setup),
        )
    )  # fmt: skip
    out_vcf = str(Path(outdir, "deepvariant.vcf.gz"))
    # Absolute, like the journal's paths, so stage files match their records.
    work = absolute_path(Path(outdir, WORK_DIRECTORY))
    # wgse-deepvariant's htslib (1.25, ARM) decodes CRAM 3.1 correctly.
    staged_reads = not native and needs_cram30(input_file)
    reads = str(work / "reads" / "reads.cram") if staged_reads else input_file
    region_files = [
        p for p in region_items(region or "", commas=False) if Path(p).is_file()
    ]
    launcher = _Launcher(
        local_dv,
        arm64_image or image,
        bool(arm64_image),
        [ref, input_file, out_vcf, str(work), checkpoint, *region_files],
        work,
        gpu_check=(
            (lambda: _check_gpu_free(not params.no_gpu_memory_check, batch_size))
            if gpu
            else None
        ),
    )
    inputs: dict[str, str | None] = {
        "alignment": input_file,
        "reference": ref,
        "launcher": local_dv if launcher.image is None else None,
        **{f"regions:{i}": path for i, path in enumerate(region_files)},
        **_checkpoint_files(checkpoint),
        **(native.weight_files() if native else {}),
    }

    with StepJournal.for_output(
        out_vcf, "deepvariant", resume_policy(params)
    ) as journal:
        shards = _shard_count(journal, int(threads))
        if native:
            batch_size = batch_size or native_deepvariant.default_batch_size()
            dv_args = _native_args(
                native, ref, reads, str(work / Path(out_vcf).name), shards, work,
                region, batch_size,
            )  # fmt: skip
        else:
            dv_args = _docker_args(
                model_type, ref, reads, str(work / Path(out_vcf).name), shards, work,
                region, checkpoint, batch_size, postprocess, int(threads),
            )  # fmt: skip
        if not journal.dry_run and not any(journal.recorded(s) for s in STEPS):
            preflight_resources(
                lambda: estimate_resources(
                    "deepvariant",
                    input_paths=[input_file],
                    output_dir=outdir,
                    reference=ref,
                    threads=threads,
                    region=region,
                    inference_batch_size=batch_size or CALL_VARIANTS_BATCH,
                    deepvariant_model=model_type,
                    postprocess_plan=postprocess,
                    available_ram=available,
                    deepvariant_native=bool(native),
                )
            )
        recorded_image: dict[str, str | None] = {"image": local_dv}
        if native:
            recorded_image["version"] = native.version
        if launcher.image:
            recorded_image = dict(image_record(launcher.image))
            _migrate_image(journal, launcher.image, recorded_image)
        logging.info(LOG_MESSAGES["vcf_calling_deepvariant"].format(output=out_vcf))
        try:
            done = _run_stages(
                journal, launcher, dv_args,
                inputs=inputs,
                params={
                    **recorded_image,
                    "model_type": model_type,
                    "shards": shards,
                },
                region=region,
                jobs=int(threads),
                staging=(
                    (lambda: stage_cram30(
                        input_file, ref, str(work / "reads"), threads, region,
                        tool="DeepVariant", why=_CRAM31_BUG,
                    )) if staged_reads else None
                ),
                out_vcf=out_vcf,
            )  # fmt: skip
        except (OSError, subprocess.SubprocessError) as e:
            raise WGSExtractError(
                f"DeepVariant failed: {e}. Its finished stages are kept in {work}; "
                "rerun the same command to resume from them."
            ) from e
    if done:
        record_variants(out_vcf, "variants", warn_if_no_variants(out_vcf, ref, region))


cmd_deepvariant = cli_handler(VcfDeepvariantParams, run_deepvariant)
STEPS = DEEPVARIANT_STEPS
WORK_DIRECTORY = ".deepvariant.work"
_COMMAND_MARK = "***** Running the command:*****"
# Google's stage tools (/opt/deepvariant/bin/make_examples) or wgse-deepvariant's
# stage subcommands (wgse-deepvariant make_examples).
_STAGE_BINARY = re.compile(r"[/ ](make_examples|call_variants|postprocess_variants)\s")
# Settings that bound a stage's memory or CPU use but not what it writes.
_RESOURCE_FLAGS = re.compile(r'\s--(?:cpus|num_partitions|batch_size)[= ]"?\d+"?')
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
    ):
        self.local = local
        self.image = image
        self.arm64 = arm64
        self.paths = paths
        self.work = work
        # A GPU image's check of the GPU, before the first stage that uses it.
        self.gpu_check = gpu_check

    def launcher(self) -> str:
        return str(self.local) if not self.image else f"{_DV_BIN}/run_deepvariant"

    def __call__(
        self, argv: list[str], *, capture: bool = False, gpu: bool = False
    ) -> str:
        """Run ``argv``; ``gpu`` gives a GPU image's container the GPUs."""
        gpu = gpu and self.gpu_check is not None
        if gpu:
            assert self.gpu_check is not None
            self.gpu_check()
            self.gpu_check = lambda: None
        if not self.image:
            # stdin closed: DeepVariant's GNU parallel would read it otherwise.
            result = run_command(
                argv, env=_dv_env(), stdin=subprocess.DEVNULL, capture_output=capture
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
) -> bool:
    """Run DeepVariant stage by stage, skipping the ones an earlier run
    finished. A killed make_examples keeps its finished shards, and runs
    ``jobs`` of them at once. Returns whether the VCF was made or kept (not
    in --dry-run)."""
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

    def step(name: str) -> Step:
        # A stage's key holds its own and every earlier stage's commands, so
        # even the final VCF, kept after the others are deleted, is redone
        # when an earlier stage's settings (such as the model) change.
        upto = STEPS[1 : STEPS.index(name) + 1]
        text = "\n".join(c for stage in upto for c in commands[stage])
        return Step(
            name,
            inputs=inputs,
            outputs=[out_vcf] if name == "postprocess_variants" else (),
            params={**params, "commands": _RESOURCE_FLAGS.sub("", text)},
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
    final = step("postprocess_variants")
    if not any(journal.policy.forced(s) for s in STEPS[:-1]):
        if not journal.check(final).run:
            journal.decide(final)
            if not journal.dry_run:
                ensure_vcf_indexed(out_vcf)
            return not journal.dry_run

    decisions: list[Decision] = []
    claimed: set[Path] = set()
    for name in STEPS:
        if name == "stage_reads" and staging is None:
            continue
        current = staging_step if name == "stage_reads" else step(name)
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
                run(["/bin/bash", "-c", command], gpu=name == "call_variants")
            if name == "postprocess_variants":
                for suffix in ("", ".tbi"):
                    source = Path(str(staged_vcf) + suffix)
                    if source.exists():
                        os.replace(source, out_vcf + suffix)
                if not Path(out_vcf).exists():
                    raise WGSExtractError("DeepVariant failed to produce output VCF.")
                ensure_vcf_indexed(out_vcf)
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
