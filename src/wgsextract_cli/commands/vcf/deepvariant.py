"""DeepVariant command orchestration; model, resource and stage helpers are split.

The existing command module retains its helper import paths as explicit aliases.
"""

import logging
import os
import platform
import subprocess
import sys
from dataclasses import replace
from functools import partial
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.vcf import VcfDeepvariantParams
from wgsextract_cli.core import native_deepvariant
from wgsextract_cli.core.alignment.cram_staging import (
    needs_cram30,
    stage_cram30,
)
from wgsextract_cli.core.config import settings as settings
from wgsextract_cli.core.containers import (
    docker_info,
    host_path,
    image_record,
)
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.native_support import is_arm64_docker_host, require_native_tool
from wgsextract_cli.core.output_checks import warn_if_no_variants
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.region_selection import (
    is_region_file,
    region_items,
)
from wgsextract_cli.core.resource_estimates import (
    GIB,
    PostprocessPlan,
    PreflightEstimate,
    check_host_requirement,
    estimate_resources,
    preflight_resources,
)
from wgsextract_cli.core.results import warn
from wgsextract_cli.core.steps import StepJournal, resume_policy
from wgsextract_cli.core.utils import UsageError, WGSExtractError
from wgsextract_cli.core.variant_files import ensure_vcf_indexed

from .basic import CallerSetup, record_variants, setup_caller
from .deepvariant_pipeline.models import (
    _ARM64_ALTERNATIVES as _ARM64_ALTERNATIVES,
)
from .deepvariant_pipeline.models import (
    _ARM64_SETUP as _ARM64_SETUP,
)
from .deepvariant_pipeline.models import (
    _SETUP as _SETUP,
)
from .deepvariant_pipeline.models import (
    DEFAULT_ARM64_IMAGE as DEFAULT_ARM64_IMAGE,
)
from .deepvariant_pipeline.models import (
    DEFAULT_IMAGE as DEFAULT_IMAGE,
)
from .deepvariant_pipeline.models import (
    DEFAULT_ULTIMA_GPU_IMAGE as DEFAULT_ULTIMA_GPU_IMAGE,
)
from .deepvariant_pipeline.models import (
    DEFAULT_ULTIMA_IMAGE as DEFAULT_ULTIMA_IMAGE,
)
from .deepvariant_pipeline.models import (
    _container_regions as _container_regions,
)
from .deepvariant_pipeline.models import (
    _dv_env as _dv_env,
)
from .deepvariant_pipeline.models import (
    _image_platform as _image_platform,
)
from .deepvariant_pipeline.models import (
    _model_type as _model_type,
)
from .deepvariant_pipeline.models import (
    _refuse_emulated_amd64 as _refuse_emulated_amd64,
)
from .deepvariant_pipeline.models import (
    _require_arm64_wgs as _require_arm64_wgs,
)
from .deepvariant_pipeline.models import (
    _require_ont_x86 as _require_ont_x86,
)
from .deepvariant_pipeline.models import (
    _verify_arm64_image as _verify_arm64_image,
)
from .deepvariant_pipeline.models import (
    _verify_docker as _verify_docker,
)
from .deepvariant_pipeline.models import (
    _verify_docker_image as _verify_docker_image,
)
from .deepvariant_pipeline.models import (
    _warn_ont_chemistry as _warn_ont_chemistry,
)
from .deepvariant_pipeline.models import (
    deepvariant_image as deepvariant_image,
)
from .deepvariant_pipeline.resources import (
    _available_memory as _available_memory,
)
from .deepvariant_pipeline.resources import (
    _docker_memory as _docker_memory,
)
from .deepvariant_pipeline.resources import (
    _host_requirement as _host_requirement,
)
from .deepvariant_pipeline.resources import (
    _postprocess_args as _postprocess_args,
)
from .deepvariant_pipeline.resources import (
    _postprocess_plan as _postprocess_plan,
)
from .deepvariant_pipeline.resources import (
    _selected_share as _selected_share,
)
from .deepvariant_pipeline.resources import (
    _supports_num_partitions as _supports_num_partitions,
)
from .deepvariant_pipeline.stages import (
    _COMMAND_MARK as _COMMAND_MARK,
)
from .deepvariant_pipeline.stages import (
    _DV_BIN as _DV_BIN,
)
from .deepvariant_pipeline.stages import (
    _JOBLOG as _JOBLOG,
)
from .deepvariant_pipeline.stages import (
    _OOM_ERROR as _OOM_ERROR,
)
from .deepvariant_pipeline.stages import (
    _REGIONS as _REGIONS,
)
from .deepvariant_pipeline.stages import (
    _RESOURCE_FLAGS as _RESOURCE_FLAGS,
)
from .deepvariant_pipeline.stages import (
    _STAGE_BINARY as _STAGE_BINARY,
)
from .deepvariant_pipeline.stages import (
    GVCF_NAME as GVCF_NAME,
)
from .deepvariant_pipeline.stages import (
    STEPS as STEPS,
)
from .deepvariant_pipeline.stages import (
    WORK_DIRECTORY as WORK_DIRECTORY,
)
from .deepvariant_pipeline.stages import (
    _checkpoint_files as _checkpoint_files,
)
from .deepvariant_pipeline.stages import (
    _docker_args as _docker_args,
)
from .deepvariant_pipeline.stages import (
    _Launcher as _Launcher,
)
from .deepvariant_pipeline.stages import (
    _migrate_image as _migrate_image,
)
from .deepvariant_pipeline.stages import (
    _native_args as _native_args,
)
from .deepvariant_pipeline.stages import (
    _run_stages as _run_stages,
)
from .deepvariant_pipeline.stages import (
    _shard_count as _shard_count,
)
from .deepvariant_pipeline.stages import (
    _stage_commands as _stage_commands,
)
from .deepvariant_pipeline.stages import (
    _stage_files as _stage_files,
)
from .reference_evidence import caller_provenance, record_gvcf

# Gate/remove this workaround when supported DV images ship htslib >=1.19
# (htscodecs >=1.5.2); a host-samtools decode cannot detect their SSE4 bug.
_CRAM31_BUG = (
    "DeepVariant's x86-64 images (htslib 1.18 / htscodecs 1.5.1) have an SSE4 "
    "rANS decoder bug that can fail on valid CRAM 3.1 blocks; staging applies "
    "to every image."
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
# Free GPU memory wgse-deepvariant's call_variants needs on CUDA at its default
# batch (#719, RTX 3090, HG002 chr20): see NATIVE_CUDA_MIN_FREE's measurement.
NATIVE_CUDA_MIN_FREE = 3 * GIB


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


def _check_gpu_free(
    check: bool,
    batch_size: int | None,
    need: int = ULTIMA_GPU_MIN_FREE,
    default_batch: int = CALL_VARIANTS_BATCH,
    device: int | None = None,
) -> None:
    """Refuse (unless not ``check``) when a GPU lacks the ``need`` bytes
    call_variants needs at its ``default_batch``; just before it runs, so
    plans and finished reruns don't need one. Every GPU is checked (the
    container gets them all), or only ``device`` (wgse-deepvariant's)."""
    from wgsextract_cli.core.parabricks import gpu_memory

    gpus = [g for g in gpu_memory() if device is None or g.index == device]
    free = [gpu.free_bytes for gpu in gpus if gpu.free_bytes is not None]
    if not free:
        logging.warning("GPU memory is unknown; running call_variants anyway.")
        return
    if check and batch_size not in (None, default_batch):
        warn(
            "deepvariant_gpu_batch_size",
            f"--batch-size {batch_size}: the {need / GIB:.1f} GiB "
            "free-GPU-memory check assumes call_variants' default batch of "
            f"{default_batch}, so it may pass a batch that runs out of GPU "
            "memory or refuse one that fits.",
        )
    least = min(free)
    if least >= need:
        return
    message = (
        f"DeepVariant call_variants needs ~{need / GIB:.1f} GiB of "
        + (
            "free memory on each GPU, but one"
            if device is None
            else f"free memory on GPU {device}, but it"
        )
        + f" has {least / GIB:.1f} GiB free "
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
    """Parabricks DeepVariant, one pbrun run per sex/PAR ploidy partition
    (caller_ploidy.call_partitioned): pbrun has --haploid-contigs but no PAR
    regions, so a man's non-PAR chrX and chrY are called apart from the rest."""
    from wgsextract_cli.core.parabricks import (
        parabricks_image,
        plan_gpu_memory,
        verify_parabricks,
    )

    from .caller_ploidy import GvcfEngine, call_partitioned

    if params.batch_size is not None:
        raise UsageError("DeepVariant --batch-size doesn't apply to Parabricks.")
    if params.dry_run or params.force_steps:
        raise UsageError(
            "--dry-run and --force-step apply to DeepVariant's stages; "
            "Parabricks (--gpu) has no resumable stages."
        )
    require_native_tool("parabricks")
    gvcf = params.reference_evidence == "gvcf"
    verify_dependencies(["samtools", "tabix", *(["bcftools"] if gvcf else [])])
    verify_parabricks()
    # Refuse a GPU or host that can't run it before any work; each partition
    # fits itself to the GPU's memory again just before it runs.
    plan_gpu_memory("deepvariant", "deepvariant", check=not params.no_gpu_memory_check)
    setup = caller or setup_caller(params)
    _require_ont_x86(model_type)
    if model_type == "ONT_R104":
        _warn_ont_chemistry(str(params.input), setup.ref)
    logging.info(
        LOG_MESSAGES["vcf_calling_deepvariant"].format(
            output=Path(setup.outdir, "deepvariant.vcf.gz")
        )
    )
    call_partitioned(
        params,
        setup,
        "deepvariant",
        partial(_pbrun_deepvariant, mode=_PARABRICKS_MODES[model_type]),
        GvcfEngine("parabricks-deepvariant", parabricks_image()) if gvcf else None,
        gpu_ploidy=True,
    )


def _pbrun_deepvariant(
    params: VcfDeepvariantParams,
    sample_ploidy: int,
    setup: CallerSetup | None,
    record: bool,
    gvcf: str | None,
    *,
    mode: list[str],
) -> None:
    """One pbrun deepvariant run over ``params.region`` into the outdir's
    ``deepvariant.vcf.gz``, and with ``gvcf`` its gVCF there. A haploid run
    lists its contigs in --haploid-contigs; its region (a partition's BED,
    which pbrun takes as --interval-file) keeps it off their PARs."""
    from wgsextract_cli.core.parabricks import call_variants, plan_gpu_memory

    assert setup is not None
    ref, region = setup.ref, params.region
    out_vcf = str(Path(setup.outdir, "deepvariant.vcf.gz"))
    options = [
        *mode,
        *plan_gpu_memory(
            "deepvariant",
            "deepvariant",
            check=not params.no_gpu_memory_check,
            report=False,
        ),
    ]
    if sample_ploidy == 1:
        assert region is not None
        contigs = dict.fromkeys(
            line.split("\t", 1)[0]
            for line in Path(region).read_text().splitlines()
            if line.strip()
        )
        options += ["--haploid-contigs", ",".join(contigs)]
    if gvcf is None:
        call_variants("deepvariant", str(params.input), ref, out_vcf, region, options)
    else:
        # One run writes both: --out-variants names the gVCF, and pbrun
        # writes the VCF beside it without the .g (X.g.vcf.gz -> X.vcf.gz).
        call_variants(
            "deepvariant", str(params.input), ref, gvcf, region,
            [*options, "--gvcf"],
        )  # fmt: skip
        variants = Path(gvcf).with_name(
            Path(gvcf).name.removesuffix(".g.vcf.gz") + ".vcf.gz"
        )
        if not variants.is_file() or not Path(gvcf).is_file():
            raise WGSExtractError(
                "Parabricks DeepVariant didn't write both its VCF and gVCF."
            )
        for suffix in ("", ".tbi"):
            if Path(f"{variants}{suffix}").exists():
                os.replace(f"{variants}{suffix}", out_vcf + suffix)
            elif suffix:
                Path(out_vcf + suffix).unlink(missing_ok=True)
    ensure_vcf_indexed(out_vcf)
    if record:
        record_variants(
            out_vcf,
            "variants",
            warn_if_no_variants(out_vcf, ref, params.region),
        )


def _native_gpu_preflight(native: native_deepvariant.NativeDeepVariant | None) -> None:
    """Before --gpu's first unfinished stage: the build has CUDA and the
    driver sees a GPU, so make_examples doesn't run for a GPU that isn't
    there. Plans and finished reruns don't need one."""
    from wgsextract_cli.core.parabricks import gpu_memory

    if native is None:
        return
    if not native.cuda:
        raise WGSExtractError(
            f"--gpu: wgse-deepvariant ({native.path}) is the CPU-only build. "
            "Install its cuda129 package, or call without --gpu."
        )
    if not any(gpu.index == 0 for gpu in gpu_memory()):
        raise WGSExtractError(
            "--gpu: nvidia-smi reports no NVIDIA GPU; install the NVIDIA "
            "driver, or call without --gpu."
        )


def _native_model(path: str, model_type: str, checkpoint: str | None) -> bool:
    """Whether wgse-deepvariant at ``path`` calls with this model here. On a
    Mac, where Google's image can't run, the others are refused; on Linux
    they run in the image (or Parabricks) as before, as do models the
    installed build predates."""
    if not checkpoint and model_type in native_deepvariant.models():
        return native_deepvariant.qualified(path, model_type)
    if sys.platform == "darwin":
        selection = "--checkpoint" if checkpoint else f"model {model_type}"
        raise WGSExtractError(
            f"wgse-deepvariant runs only the WGS model; DeepVariant {selection} "
            f"isn't qualified on Apple Silicon. {_ARM64_ALTERNATIVES}"
        )
    return False


def run_deepvariant(params: VcfDeepvariantParams) -> None:
    """``vcf deepvariant``: DeepVariant into ``deepvariant.vcf.gz``, in
    resumable stages, or Parabricks."""
    require_native_tool("run_deepvariant")
    batch_size = params.batch_size
    if batch_size is not None and batch_size < 1:
        raise UsageError("DeepVariant --batch-size must be positive.")
    caller = None
    gpu = params.gpu
    native_gpu = False
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
        # wgse-deepvariant, when installed, calls its models on the GPU
        # without Docker; Parabricks the rest (deepvariant_native = "off": all).
        installed = native_deepvariant.installed()
        native_gpu = installed is not None and _native_model(installed, model, None)
        if native_gpu:
            logging.info(
                "Calling on the GPU with wgse-deepvariant (CUDA), without Docker "
                'or Parabricks; deepvariant_native = "off" runs Parabricks instead.'
            )
        elif model != "ULTIMA":
            _deepvariant_parabricks(params, model, caller)
            return
        else:
            # Parabricks has no ULTIMA model: Google's GPU image runs
            # DeepVariant's stages, as on the CPU, with call_variants on the GPU.
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
    native_path = native_deepvariant.installed() if native_gpu or not gpu else None
    native = None
    early_model = _model_type(params)
    if native_path and sys.platform != "darwin" and not _model_chosen(params):
        # On Linux the reads choose between wgse-deepvariant and the image,
        # before the build is checked.
        caller = caller or setup_caller(params)
        early_model = _model_type(params, caller.ref, caller.platform)
    if native_path and _native_model(native_path, early_model, checkpoint):
        native = native_deepvariant.require(
            native_path,
            early_model,
            weights=_model_chosen(params) or sys.platform != "darwin",
        )
    # The GPU image (Ultima) or Parabricks.
    gpu_image = gpu and not native
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
        if arm64_image or gpu_image
        else get_tool_path("run_deepvariant")
    )
    if not local_dv and not arm64_image and not gpu_image:
        _verify_docker()
    gvcf = params.reference_evidence == "gvcf"
    verify_dependencies(["samtools", "tabix", *(["bcftools"] if gvcf else [])])
    caller = caller or setup_caller(params)
    threads, outdir, ref = caller.threads, caller.outdir, caller.ref
    input_file, region = str(params.input), params.region
    model_type = _model_type(params, ref, caller.platform)
    if arm64_image:
        _require_arm64_wgs(model_type, checkpoint)
    if native and (model_type != native.model_type or native.weights is None):
        # The reads chose the model.
        if _native_model(native.path, model_type, checkpoint):
            native = native_deepvariant.require(native.path, model_type)
        else:
            native = None
            local_dv = get_tool_path("run_deepvariant")
            if not local_dv:
                _verify_docker(model_type)
    _require_ont_x86(model_type)
    if model_type == "ONT_R104":
        _warn_ont_chemistry(input_file, ref)
    if model_type == "ULTIMA" and local_dv:
        # A local run_deepvariant is a release, which has no ULTIMA model yet.
        _verify_docker(model_type)
        local_dv = None
    image = arm64_image or (
        None if local_dv else _verify_docker_image(model_type, gpu_image)
    )
    setup = (
        _ARM64_SETUP
        if arm64_image
        else (_ULTIMA_GPU_SETUP if gpu_image else _SETUP).format(image=image)
        if image
        else None
    )
    engine = _docker_memory(setup)
    available = _available_memory(engine)
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
    out_gvcf = str(Path(outdir, GVCF_NAME)) if gvcf else None
    # Absolute, like the journal's paths, so stage files match their records.
    work = absolute_path(Path(outdir, WORK_DIRECTORY))
    from .caller_ploidy import calling_ploidy, require_diploid, write_intervals

    ploidy = calling_ploidy(input_file, ref, caller.lib, outdir, region, commas=False)
    called_region = region
    if native:
        require_diploid(ploidy, "wgse-deepvariant")
    if not any(i.ploidy for i in ploidy):
        raise WGSExtractError("The sex/ploidy policy excludes every requested base.")
    if any(i.ploidy == 0 for i in ploidy):
        region = " ".join(
            f"{i.chrom}:{i.start + 1}-{i.end}" for i in ploidy if i.ploidy
        )
        if len(region.encode()) > 500_000:
            raise WGSExtractError(
                "Ploidy exclusions require too many calling intervals; "
                "select a smaller --region."
            )
    haploid = sorted({i.chrom for i in ploidy if i.ploidy == 1})
    par = None
    if haploid:
        par_intervals = [i for i in ploidy if i.chrom in haploid and i.ploidy == 2]
        if par_intervals:
            par = write_intervals(
                absolute_path(Path(outdir, ".deepvariant.policy", "par.bed")),
                par_intervals,
            )
    # wgse-deepvariant's htslib (1.25, ARM) decodes CRAM 3.1 correctly.
    staged_reads = not native and needs_cram30(input_file)
    reads = str(work / "reads" / "reads.cram") if staged_reads else input_file
    region_files = [
        p for p in region_items(region or "", commas=False) if is_region_file(p)
    ]
    launcher = _Launcher(
        local_dv,
        arm64_image or image,
        bool(arm64_image),
        [ref, input_file, out_vcf, str(work), checkpoint, *region_files, par],
        work,
        preflight=(lambda: _native_gpu_preflight(native)) if native_gpu else None,
        gpu_check=(
            (
                lambda: _check_gpu_free(
                    not params.no_gpu_memory_check,
                    batch_size,
                    NATIVE_CUDA_MIN_FREE,
                    native_deepvariant.default_batch_size(),
                    device=0,
                )
            )
            if native_gpu
            else (lambda: _check_gpu_free(not params.no_gpu_memory_check, batch_size))
            if gpu
            else None
        ),
    )
    inputs: dict[str, str | None] = {
        "alignment": input_file,
        "reference": ref,
        "ploidy_par": par,
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
                region, batch_size, native_gpu, int(threads),
                str(work / GVCF_NAME) if gvcf else None,
            )  # fmt: skip
        else:
            dv_args = _docker_args(
                model_type, ref, reads, str(work / Path(out_vcf).name), shards, work,
                region, checkpoint, batch_size, postprocess, int(threads),
                str(work / GVCF_NAME) if gvcf else None,
            )  # fmt: skip
            if haploid:
                dv_args += ["--haploid_contigs", ",".join(haploid)]
                if par is not None:
                    dv_args += ["--par_regions_bed", host_path(par)]

        def preflight(fresh: bool) -> None:
            """State the host requirement; refuse a shortfall only when
            ``fresh`` (no stage is kept): kept stages need less, so it warns."""

            def estimate_with(
                threads: str, batch: int, plan: PostprocessPlan | None
            ) -> PreflightEstimate:
                return estimate_resources(
                    "deepvariant",
                    input_paths=[input_file],
                    output_dir=outdir,
                    reference=ref,
                    threads=threads,
                    region=region,
                    inference_batch_size=batch,
                    deepvariant_model=model_type,
                    postprocess_plan=plan,
                    available_ram=available,
                    deepvariant_native=bool(native),
                    reference_evidence=gvcf,
                )

            batch = batch_size or CALL_VARIANTS_BATCH
            estimate = preflight_resources(
                lambda: estimate_with(threads, batch, postprocess)
            )
            try:
                # The least it can run in: one thread, a batch of 256.
                floor: PreflightEstimate | None = estimate_with(
                    "1", min(batch, 256), None
                )
            except (OSError, ValueError):
                floor = None
            check_host_requirement(
                _host_requirement(
                    estimate, floor, input_file=input_file, ref=ref, region=region,
                    threads=int(threads), model_type=model_type,
                    native=bool(native), native_gpu=native_gpu,
                    staged_reads=staged_reads, work=work, engine=engine,
                ),
                enforce=fresh and not params.no_preflight,
                unenforced="--no-preflight" if fresh else "finished stages are kept",
                disk_advice="Free up space, choose an --outdir on a bigger disk "
                "or call a --region.",
                memory_advice="Lower --batch-size or --threads.",
            )  # fmt: skip

        recorded_image: dict[str, str | None] = {"image": local_dv}
        if native:
            recorded_image["version"] = native.version
        if launcher.image:
            recorded_image = dict(image_record(launcher.image))
            _migrate_image(journal, launcher.image, recorded_image)
        evidence = None
        if out_gvcf:
            if native:
                version = f"wgse-deepvariant {native.version}"
            elif launcher.image:
                version = str(recorded_image["image"])
            else:
                version = f"run_deepvariant ({local_dv})"
            evidence = (
                out_gvcf,
                caller_provenance("deepvariant", version, ploidy, called_region),
            )
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
                evidence=evidence,
                preflight=None if journal.dry_run else preflight,
            )  # fmt: skip
        except (OSError, subprocess.SubprocessError) as e:
            raise WGSExtractError(
                f"DeepVariant failed: {e}. Its finished stages are kept in {work}; "
                "rerun the same command to resume from them."
            ) from e
    if done:
        record_variants(out_vcf, "variants", warn_if_no_variants(out_vcf, ref, region))
        if out_gvcf:
            record_gvcf(out_gvcf)


cmd_deepvariant = cli_handler(VcfDeepvariantParams, run_deepvariant)
