from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, TypeVar

import psutil

from wgsextract_cli.api._params import job_values
from wgsextract_cli.api.align import ALIGNERS as ALIGNERS
from wgsextract_cli.api.align import PLATFORMS as PLATFORMS
from wgsextract_cli.api.align import AlignParams
from wgsextract_cli.cli.registration.align import register as register
from wgsextract_cli.core import bwa_mem2, dense_suffix_array, ultima
from wgsextract_cli.core.alignment.fastq import (
    check_primary_reads,
    fastq_input,
    stage_read_input,
    validate_fastq,
)
from wgsextract_cli.core.alignment.memory import (
    BWA_MEM3_CUDA_BYTES,
    BWA_MEM3_METAL_BYTES,
    MINIMAP2_CUDA_BYTES,
    available_memory,
    bwa_gpu_memory,
    check_mapping_memory,
    estimate_mapping_memory,
)
from wgsextract_cli.core.alignment.profiles import (
    output_profile_options,
    validate_output_profile,
)
from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.atomic_output import (
    atomic_output_mode,
    atomic_outputs_enabled,
    safe_output,
)
from wgsextract_cli.core.chunked_alignment import Chunk, ChunkedAlignment
from wgsextract_cli.core.config import settings, typed_settings
from wgsextract_cli.core.dependencies import get_tool_path as get_tool_path
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.genome_library import pair_stem
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.native_aligners import (
    BWA_MEM3_COMPAT,
    BWA_MEM3_FAST,
    INSTALL_HINT,
    AlignerCommand,
    NativeAligner,
    NativeMemoryDecision,
    bwa_mem3_failure,
    find_bwa_mem3,
    find_native_aligner,
    native_gpu_preference,
    native_setting,
    platform_binaries,
)
from wgsextract_cli.core.native_support import require_native_tool
from wgsextract_cli.core.paths import absolute_path, java_local_path
from wgsextract_cli.core.pixi_tools import pixi_install_command
from wgsextract_cli.core.read_counts import (
    fastq_input_statistics,
)
from wgsextract_cli.core.reference_assets import (
    is_fasta_path,
    reference_genomes_dir,
)
from wgsextract_cli.core.reference_resolver import ReferenceLibrary
from wgsextract_cli.core.remote_input import is_url
from wgsextract_cli.core.resource_estimates import (
    estimate_resources,
    preflight_resources,
)
from wgsextract_cli.core.resource_policy import stage_thread_plan
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.samtools_commands import (
    parse_memory,
    regular_files_bytes,
    sort_memory,
)
from wgsextract_cli.core.staged_alignment import workspace_path as workspace_path
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    get_resource_defaults,
    get_sam_sort_cmd,
    run_command,
    run_pipeline,
)
from wgsextract_cli.core.variant_files import (
    calculate_bam_md5,
    resolve_reference,
    verify_paths_exist,
)
from wgsextract_cli.core.warnings import (
    indexed_read_count,
    print_warning,
    warn_alignment_sort_space,
)

from ._extract_helpers import cleanup_files
from .alignment.output import (
    _ensure_read_group_sample as _ensure_read_group_sample,
)
from .alignment.output import (
    _index_output as _index_output,
)
from .alignment.output import (
    _sort_and_mark_stages as _sort_and_mark_stages,
)
from .alignment.output import (
    record_alignment as record_alignment,
)
from .alignment.output import (
    run_streamed_alignment as run_streamed_alignment,
)
from .alignment.read_groups import _RG_PLATFORMS as _RG_PLATFORMS
from .alignment.read_groups import _ultima_read_group as _ultima_read_group
from .alignment.read_groups import read_group as read_group
from .alignment.read_groups import read_group_descriptions as read_group_descriptions
from .alignment.read_groups import ultima_read_groups as ultima_read_groups
from .alignment.reads import _READ_FILE_SUFFIXES as _READ_FILE_SUFFIXES
from .alignment.reads import LONG_READ_TAGS as LONG_READ_TAGS
from .alignment.reads import _alignment_file_platform as _alignment_file_platform
from .alignment.reads import _cram_reference as _cram_reference
from .alignment.reads import _is_alignment_file as _is_alignment_file
from .alignment.reads import _long_read_length as _long_read_length
from .alignment.reads import _needs_read_extraction as _needs_read_extraction
from .alignment.reads import (
    _refuse_short_read_alignment_file as _refuse_short_read_alignment_file,
)
from .alignment.reads import _refuse_unreadable_reads as _refuse_unreadable_reads
from .alignment.reads import long_read_fastq_command as long_read_fastq_command
from .alignment.reads import reads_stem as reads_stem
from .alignment.reads import resolve_platform as resolve_platform
from .alignment.selection import _LONG_READ_PLATFORMS as _LONG_READ_PLATFORMS
from .alignment.selection import _MINIMAP2_PRESET_ALIASES as _MINIMAP2_PRESET_ALIASES
from .alignment.selection import _PBMM2_PRESETS as _PBMM2_PRESETS
from .alignment.selection import BWA_ALIGNERS as BWA_ALIGNERS
from .alignment.selection import PACBIO_PLATFORMS as PACBIO_PLATFORMS
from .alignment.selection import _minimap2_preset as _minimap2_preset
from .alignment.selection import is_long_read as is_long_read
from .alignment.selection import requested_aligner as requested_aligner
from .alignment.selection import select_aligner as select_aligner
from .alignment.selection import short_read_tool as short_read_tool

if TYPE_CHECKING:
    from wgsextract_cli.api._params import Params

# BWA's default batch size depends on threads and changes pairing statistics.
BWA_BATCH_BASES = "100000000"


class InputReads(NamedTuple):
    """What an earlier stage measured of the reads: their number and mean
    length (each None when unknown)."""

    reads: int | None
    read_length: float | None = None


_Job = TypeVar("_Job", bound="AlignJob")


@dataclass(kw_only=True)
class AlignJob:
    """One alignment: its settings, with paths as strings, and what earlier
    stages learnt about its reads. ``align`` builds one from ``AlignParams``;
    realign one from ``RealignParams``, pointing ``r1``/``r2`` at the reads it
    extracted or streams."""

    r1: str | None = None
    r2: str | None = None
    input: str | None = None
    ref: str | None = None
    input_ref: str | None = None
    outdir: str | None = None
    threads: int | None = None
    memory: str | None = None
    memory_explicit: bool = False
    platform: str | None = None
    aligner: str = "auto"
    preset: str | None = None
    gpu: bool = False
    no_gpu_memory_check: bool = False
    sample: str | None = None
    long_read: bool = False
    format: str = "BAM"
    output_profile: str | None = None
    # bwa-mem3's --fast speed preset, whose alignments differ from BWA's.
    fast: bool = False
    # auto or off: WGS Extract's Apple Silicon aligner builds (native_aligners).
    native_aligners: str | None = None
    prefer_native_gpu: bool | None = None
    # Whether wgse-bwa reads the reference's dense suffix-array file (opt-in).
    dense_suffix_array: bool | None = None
    # samtools options decoding a CRAM r1 (realign's, or _cram_reference's).
    input_cram_opt: list[str] | None = None
    # Set once measured, so later stages don't read the reads again.
    input_reads: InputReads | None = None
    # The source BAM/CRAM's read-group DS, when realign aligns its FASTQ.
    read_group_description: str = ""
    # realign's chunks of a sorted long-read r1 (chunked_alignment), and
    # whether an interrupted chunked run's work may be resumed.
    chunks: list[Chunk] | None = None
    resume_chunks: bool = False
    # What the chunks read: r1, or a remote r1 with its local index.
    chunk_input: str | None = None
    exact_reads: int | None = None
    read_cleanup: ExitStack | None = None
    retire_fastqs: tuple[str, ...] = ()

    @classmethod
    def of(cls: type[_Job], params: Params) -> _Job:
        """The job of ``params``' fields that it shares."""
        job = cls(
            **job_values(params, cls), memory_explicit=params.explicitly_set("memory")
        )
        if params.COMMAND not in ("align", "realign"):
            return job
        job.output_profile = job.output_profile or typed_settings().output_profile
        output_profile_options(job.output_profile)
        job.format = job.format or "BAM"
        if job.output_profile != "default":
            if getattr(params, "format", None) == "BAM":
                if params.explicitly_set("output_profile"):
                    raise UsageError(
                        f"--output-profile {job.output_profile} writes CRAM, not "
                        "explicit --format BAM. Use --output-profile default "
                        "for BAM, or --format CRAM."
                    )
                logging.info(
                    "Using explicit --format BAM instead of config output_profile=%s.",
                    job.output_profile,
                )
                job.output_profile = "default"
            else:
                job.format = "CRAM"
        current_result().add_metrics(output_profile=job.output_profile)
        return job

    @property
    def read_count(self) -> int | None:
        return self.input_reads.reads if self.input_reads else None


def run_align(params: AlignParams) -> None:
    """The ``align`` operation (``api.align.align``)."""
    add_read_inputs(params.r1, params.r2)
    job = AlignJob.of(params)
    for field in ("r1", "r2"):
        path = getattr(job, field)
        if path:
            setattr(job, field, str(Path(path).expanduser()))
    # Detecting the first read name would consume a one-shot stream.
    # Long-read streams need an explicit platform before choosing their tools.
    if job.r1 and not Path(job.r1).is_file() and not job.platform:
        job.platform = "illumina"
    with ExitStack() as cleanup, atomic_output_mode(True):
        job.read_cleanup = cleanup
        run(job)


def add_read_inputs(r1: Path | None, r2: Path | None) -> None:
    """Record the reads that exist, as dispatch does for CLI arguments."""
    result = current_result()
    for path, role in ((r1, "fastq_r1"), (r2, "fastq_r2")):
        if path and path.expanduser().exists():
            result.add_input(path, role)


# Parabricks minimap2 presets for this command's long-read presets.
_GPU_MINIMAP2_PRESETS = {"map-hifi": "map-hifi", "map-ont": "map-ont"}


def run(job: AlignJob) -> None:
    _cram_reference(job)
    job.platform = _alignment_file_platform(job) or resolve_platform(
        job.platform, job.r1, job.input_ref, job.r2
    )
    aligner = select_aligner(job)
    if job.platform == "ultima":
        align_ultima(job, job.input_cram_opt)
        return
    _refuse_unreadable_reads(job, aligner)
    # pbmm2 reads a PacBio BAM itself, whatever platform was resolved.
    if not is_long_read(job) and aligner != "pbmm2":
        _refuse_short_read_alignment_file(job)
    if job.gpu:
        _run_gpu(job, aligner)
        return
    if aligner == "pbmm2":
        align_pbmm2(job)
    elif aligner == "minimap2":
        align_minimap2(job)
    else:
        align_bwa(job)


def short_read_aligner(
    requested: str,
    ref: str,
    *,
    threads: str = "1",
    alignment_threads: str | None = None,
    memory: str = "512M",
    samblaster: bool = True,
    reads: int | None = None,
    native_aligners: str | None = "off",
) -> str:
    """bwa or bwa-mem2 for --aligner bwa, bwa-mem2 or auto. Auto takes bwa-mem2,
    which is faster than bwa but writes different records, when it will run
    (upstream bwa-mem2, or with ``native_aligners`` wgse-bwa-mem2 0.5.0 and
    later, which needs no upstream bwa-mem2) and the reference has the index
    it reads that fits in this computer's memory (that build's estimate leaves
    out the .0123 file; AlignerCommand.memory_model)."""
    if requested != "auto":
        return requested
    model = bwa_mem2_memory_model(native_aligners)
    if model == "bwa-mem2" and get_tool_path("bwa-mem2") is None:
        return "bwa"
    memory = short_read_sort_memory("bwa-mem2", memory, log=False)
    needed = estimate_mapping_memory(
        ref,
        model,
        threads=threads,
        alignment_threads=alignment_threads,
        memory=memory,
        samblaster=samblaster,
        reads=reads,
    ).total
    fits = available_memory() >= needed
    if bwa_mem2_index_ready(model, ref):
        if fits:
            if model == "wgse-bwa-mem2":
                logging.info(
                    "Auto chose bwa-mem2 (records differ from bwa's): "
                    "wgse-bwa-mem2 0.5.0 or later maps without the .0123 file "
                    f"and fits in {needed / 1024**3:.1f} GiB. Use --aligner bwa "
                    "to keep bwa's records."
                )
            return "bwa-mem2"
        logging.info(
            f"Not using {model}: the alignment pipeline needs about "
            f"{needed / 1024**3:.1f} GiB of available RAM. Use --aligner bwa "
            "or lower --memory."
        )
    elif fits:
        logging.info(
            "bwa-mem2 would align this about a third faster; "
            f"`wgsextract ref index --aligner bwa-mem2 --ref {ref}` sets it up."
        )
    return "bwa"


def short_read_command(
    job: AlignJob,
    ref: str,
    *,
    threads: str,
    memory: str,
    samblaster: bool,
    reads: int | None,
    read_length: float | None,
    additional_ram_bytes: int = 0,
    announce: bool = True,
) -> tuple[AlignerCommand, str]:
    """How to run --aligner bwa, bwa-mem2 or auto (short_read_aligner's choice,
    or an Apple Silicon build in its place; native_short_read_aligner), and
    the sort memory per thread to use. Without ``announce``, a plan made
    before the alignment (realign's) logs less."""
    tool = short_read_aligner(
        requested_aligner(job),
        ref,
        threads=threads,
        alignment_threads=str(stage_thread_plan(job.threads).alignment),
        memory=memory,
        samblaster=samblaster,
        reads=reads,
        native_aligners=job.native_aligners,
    )
    if tool == "bwa-mem2":
        ensure_fasta_index(ref)
    return native_short_read_aligner(
        job,
        tool,
        ref,
        threads=threads,
        memory=short_read_sort_memory(tool, memory, log=announce),
        samblaster=samblaster,
        reads=reads,
        read_length=read_length,
        additional_ram_bytes=additional_ram_bytes,
        announce=announce,
    )


def stand_in_label(aligner: AlignerCommand) -> str | None:
    """How memory checks name a build running in another aligner's place."""
    if aligner.engine == aligner.tool:
        return None
    return f"{aligner.binary} (in place of {aligner.tool})"


class StreamingMemoryUnavailable(WGSExtractError):
    """Retry file extraction before starting an overlapping alignment pipeline."""


def streaming_mapping_fits(
    ref: str,
    tool: str,
    *,
    threads: str,
    alignment_threads: str | None = None,
    memory: str,
    samblaster: bool,
    reads: int | None,
    platform: str | None,
    read_length: float | None,
    additional_ram_bytes: int,
    gpu_bytes: int = 0,
    dense_sa_bytes: int = 0,
    staged: bool = False,
) -> bool:
    estimate = estimate_mapping_memory(
        ref,
        tool,
        threads=threads,
        alignment_threads=alignment_threads,
        memory=short_read_sort_memory(tool, memory, log=False),
        samblaster=samblaster,
        reads=reads,
        platform=platform,
        read_length=read_length,
        gpu_bytes=gpu_bytes,
        dense_sa_bytes=dense_sa_bytes,
        staged=staged,
    )
    return estimate.total + additional_ram_bytes <= available_memory()


def short_read_sort_memory(aligner: str, memory: str, *, log: bool = True) -> str:
    """Keep more memory for MEM2's index without exceeding the user's budget."""
    if aligner != "bwa-mem2":
        return memory
    amount, unit = parse_memory(memory)
    if amount * (1024 if unit == "G" else 1) > 256:
        if log:
            logging.info(
                f"Capping bwa-mem2 sort memory at 256M per thread (budget {memory})."
            )
        return "256M"
    return memory


# Memory left for the OS and other applications when wgse-bwa's GPU backend
# (Metal or CUDA) must fit.
_GPU_HEADROOM_BYTES = 2 * 1024**3
_GPU_SORT_MEMORY_STEPS_MIB = (768, 512, 384, 256)


def native_short_read_aligner(
    job: AlignJob,
    tool: str,
    ref: str,
    *,
    threads: str,
    memory: str,
    samblaster: bool,
    reads: int | None,
    read_length: float | None,
    additional_ram_bytes: int = 0,
    announce: bool = True,
) -> tuple[AlignerCommand, str]:
    """How to run bwa or bwa-mem2, and the sort memory per thread to use.

    Its build (wgse-bwa, wgse-bwa-mem2) writes the same alignments; it runs on
    its GPU (Metal on Apple Silicon, CUDA on Linux) when it can. wgse-bwa's GPU
    backend needs bwa_gpu_memory more RAM: when the pipeline's estimate with it
    exceeds physical RAM less headroom or the RAM available now, sort memory
    is lowered without changing the output. When even 256M doesn't fit,
    wgse-bwa runs on the CPU, whose buffers are not wired for GPU access.
    An over-budget CPU fallback also caps sort memory at 256M per thread.
    Workers and the scientific batch size are unchanged. Streaming realign
    counts additional_ram_bytes beside the same pipeline.
    wgse-bwa-mem2 keeps bwa-mem2's (CPU) estimate, which also covers its CPU
    fallback when the GPU can't hold its index tables; from 0.5.0 that
    estimate leaves out the .0123 file it no longer loads
    (AlignerCommand.memory_model).
    bwa-mem3 (wgse-bwa-mem3 on Apple Silicon, Bioconda's on Linux) writes
    either's exact records faster, so it runs in their place when it fits
    (bwa_mem3_stand_in), ahead of a GPU build.
    wgse-bwa reads the reference's dense suffix-array file only when the run
    asks (job.dense_suffix_array, dense_suffix_array.for_alignment); it
    counts as index memory.
    """

    # bwa-mem3 goes first, then the build (on its GPU when it fits), then
    # upstream. Even on Linux with an NVIDIA GPU: on an RTX 3090 and a Ryzen 5
    # 5600X, bwa-mem3 --compat=bwa-mem on the CPU aligned 5M pairs in 98 s
    # against wgse-bwa --cuda's 131 s, with the same records (#736).
    stand_in = bwa_mem3_stand_in(
        job,
        tool,
        ref,
        threads=threads,
        memory=memory,
        samblaster=samblaster,
        reads=reads,
        read_length=read_length,
        additional_ram_bytes=additional_ram_bytes,
    )
    dense = bool(job.dense_suffix_array)
    if stand_in is not None:
        if announce:
            dense_suffix_array.other_aligner(stand_in.binary, enabled=dense)
            stand_in.announce()
        return stand_in, memory
    native = find_native_aligner(tool, job.native_aligners)
    if tool != "bwa":
        if announce:
            aligner_name = native.binary if native else tool
            dense_suffix_array.other_aligner(aligner_name, enabled=dense)
        dense = False
    dense_sa = dense_suffix_array.for_alignment(native, ref, enabled=dense)
    if native is None:
        return AlignerCommand(tool), memory
    if native.gpu is None:
        aligner = AlignerCommand(tool, native, dense_sa=dense_sa)
    elif tool != "bwa":
        aligner = AlignerCommand(tool, native, gpu=True)
    else:
        aligner, memory = _bwa_gpu_fit(
            job,
            native,
            ref,
            threads=threads,
            memory=memory,
            samblaster=samblaster,
            reads=reads,
            read_length=read_length,
            additional_ram_bytes=additional_ram_bytes,
            dense_sa=dense_sa,
        )
    if announce:
        aligner.announce()
    return aligner, memory


def _bwa_gpu_fit(
    job: AlignJob,
    native: NativeAligner,
    ref: str,
    *,
    threads: str,
    memory: str,
    samblaster: bool,
    reads: int | None,
    read_length: float | None,
    additional_ram_bytes: int,
    dense_sa: Path | None,
) -> tuple[AlignerCommand, str]:
    """wgse-bwa on its GPU with the sort memory per thread that lets it fit
    (see native_short_read_aligner), else on the CPU with bounded sort memory."""
    tool = "bwa"
    gpu = bwa_gpu_memory(ref, reads, dense_sa_file=dense_sa is not None)
    dense_sa_bytes = dense_sa.stat().st_size if dense_sa else 0
    budget = _gpu_budget(additional_ram_bytes)
    amount, unit = parse_memory(memory)
    mib = amount * (1024 if unit == "G" else 1)
    candidates = [memory] + [
        f"{step}M" for step in _GPU_SORT_MEMORY_STEPS_MIB if step < mib
    ]

    def needed(sort_memory: str, gpu_bytes: int = gpu) -> int:
        estimate = estimate_mapping_memory(
            ref,
            tool,
            threads=threads,
            alignment_threads=str(
                stage_thread_plan(job.threads, metal=bool(gpu_bytes)).alignment
            ),
            memory=sort_memory,
            samblaster=samblaster,
            reads=reads,
            platform=job.platform,
            read_length=read_length,
            gpu_bytes=gpu_bytes,
            dense_sa_bytes=dense_sa_bytes,
        )
        return estimate.total + additional_ram_bytes

    fitting = next((m for m in candidates if needed(m) <= budget), None)
    if fitting is None and native_gpu_preference(job.prefer_native_gpu):
        preferred_memory = memory if job.memory_explicit or mib <= 256 else "256M"
        logging.info(
            "Preferring %s's native GPU (--prefer-native-gpu): estimated %.1f GiB "
            "against a %.1f GiB memory budget after GPU sort step-down; using %s. "
            "This speed preference may cause heavy swapping.",
            native.binary,
            needed(preferred_memory) / 1024**3,
            budget / 1024**3,
            preferred_memory,
        )
        return AlignerCommand(
            tool,
            native,
            gpu=True,
            dense_sa=dense_sa,
            memory_decision=NativeMemoryDecision(
                "prefer_gpu", needed(preferred_memory), budget, memory, preferred_memory
            ),
        ), preferred_memory

    if fitting is None:
        requested_memory = memory
        gpu_estimate = needed(candidates[-1])
        staged_estimate = None
        if native.gpu == "metal" and samblaster:
            chunk_memory = (
                memory if job.memory_explicit or settings.get("memory_limit") else "2G"
            )
            chunk_amount, chunk_unit = parse_memory(chunk_memory)
            if chunk_amount * (1024 if chunk_unit == "G" else 1) > 2048:
                chunk_memory = "2G"
            chunk_amount, chunk_unit = parse_memory(chunk_memory)
            chunk_mib = chunk_amount * (1024 if chunk_unit == "G" else 1)
            chunk_candidates = [chunk_memory] + [
                f"{step}M" for step in (1024, 512, 256) if step < chunk_mib
            ]
            for chunk_memory in chunk_candidates:
                staged_estimate = (
                    estimate_mapping_memory(
                        ref,
                        tool,
                        threads=threads,
                        alignment_threads=str(
                            stage_thread_plan(job.threads, metal=True).alignment
                        ),
                        memory=chunk_memory,
                        samblaster=True,
                        reads=reads,
                        platform=job.platform,
                        read_length=read_length,
                        gpu_bytes=gpu,
                        dense_sa_bytes=dense_sa_bytes,
                        staged=True,
                    ).total
                    + additional_ram_bytes
                )
                if staged_estimate <= budget:
                    break
            assert staged_estimate is not None
            if staged_estimate <= budget:
                logging.info(
                    "Separating Metal alignment from duplicate marking/sorting: "
                    "estimated %.1f GiB against %.1f GiB; compressed staged checkpoints.",
                    staged_estimate / 1024**3,
                    budget / 1024**3,
                )
                return AlignerCommand(
                    tool,
                    native,
                    gpu=True,
                    dense_sa=dense_sa,
                    staged=True,
                    memory_decision=NativeMemoryDecision(
                        "memory_budget_metal_staged",
                        staged_estimate,
                        budget,
                        memory,
                        chunk_memory,
                        staged_estimate,
                    ),
                ), chunk_memory
        logging.info(
            f"Running {native.binary} on the CPU: with its {_gpu_name(native)} GPU backend "
            f"the alignment is estimated to need {gpu_estimate / 1024**3:.1f} "
            f"GiB, more than the {budget / 1024**3:.1f} GiB memory budget. "
            "CPU fallback was about 2.5x slower on the measured 16 GB M1 whole "
            "genome. Use --prefer-native-gpu (config prefer_native_gpu=true) "
            "to favor speed instead, accepting possible heavy swapping."
        )
        if mib > 256 and needed(memory, 0) > budget:
            logging.info(
                "Sorting with 256M per thread (not %s) to reduce the over-budget "
                "CPU pipeline's concurrent memory; workers and batch size are unchanged.",
                memory,
            )
            memory = "256M"
        aligner = AlignerCommand(
            tool,
            native,
            dense_sa=dense_sa,
            memory_decision=NativeMemoryDecision(
                "memory_budget_cpu",
                gpu_estimate,
                budget,
                requested_memory,
                memory,
                staged_estimate,
            ),
        )
    else:
        if fitting != memory:
            logging.info(
                f"Sorting with {fitting} per thread (not {memory}) to leave "
                f"room for {native.binary}'s {_gpu_name(native)} GPU backend; the output "
                "is the same."
            )
        decision = (
            NativeMemoryDecision(
                f"memory_budget_{native.gpu}_low_sort",
                needed(fitting),
                budget,
                memory,
                fitting,
            )
            if fitting != memory
            else None
        )
        aligner = AlignerCommand(
            tool, native, gpu=True, dense_sa=dense_sa, memory_decision=decision
        )
        memory = fitting
    return aligner, memory


def _gpu_name(native: NativeAligner) -> str:
    return "CUDA" if native.gpu == "cuda" else "Metal"


def _gpu_budget(additional_ram_bytes: int) -> int:
    """The RAM an aligner's GPU backend may count on: this machine's less
    headroom, and for streaming realign (additional_ram_bytes) no more than is
    available now."""
    budget = psutil.virtual_memory().total - _GPU_HEADROOM_BYTES
    if additional_ram_bytes:
        budget = min(budget, available_memory())
    return int(budget)


def bwa_mem3_build(job: AlignJob, tool: str, ref: str) -> NativeAligner | None:
    """The bwa-mem3 (wgse-bwa-mem3 on Apple Silicon and Linux x86-64, else
    Bioconda's on Linux: find_bwa_mem3) that can write ``tool``'s exact
    records (--compat) for this run, memory aside, or None. It needs the reference's bwa-mem2 index.
    Ultima reads keep BWA, which is qualified for their flow tags."""
    if tool not in BWA_MEM3_COMPAT or job.platform == "ultima":
        return None
    if not bwa_mem2.has_bwa_mem3_index(ref):
        return None
    return find_bwa_mem3(job.native_aligners, instead=tool)


def bwa_mem3_stand_in(
    job: AlignJob,
    tool: str,
    ref: str,
    *,
    threads: str,
    memory: str,
    samblaster: bool,
    reads: int | None,
    read_length: float | None,
    additional_ram_bytes: int = 0,
) -> AlignerCommand | None:
    """bwa-mem3 writing bwa's or bwa-mem2's exact records (--compat) in
    place of ``tool`` (bwa_mem3_build), or None when its pipeline won't fit.

    In bwa's place it needs more memory than bwa, so its estimate must fit
    the RAM available now; otherwise (on small Macs) wgse-bwa or bwa aligns.
    In bwa-mem2's place it is taken when it needs no more than the bwa-mem2
    that would run instead (wgse-bwa-mem2 0.5.0 and later without the .0123
    file), or fits this Mac's RAM less headroom (as wgse-bwa's GPU backend must);
    an explicit --aligner bwa-mem2 then still stops when it doesn't fit the
    RAM available (_bwa_setup's check_mapping_memory). When auto chose
    bwa-mem2 it must fit the RAM available, as auto's choice did."""
    native = bwa_mem3_build(job, tool, ref)
    if native is None:
        return None
    aligner = AlignerCommand(
        tool,
        native,
        gpu=native.gpu is not None,
        options=(f"--compat={BWA_MEM3_COMPAT[tool]}",),
    )

    def needed(engine: str, gpu_bytes: int = 0) -> int:
        estimate = estimate_mapping_memory(
            ref,
            engine,
            threads=threads,
            alignment_threads=str(
                stage_thread_plan(job.threads, metal=bool(gpu_bytes)).alignment
            ),
            memory=memory,
            samblaster=samblaster,
            reads=reads,
            platform=job.platform,
            read_length=read_length,
            gpu_bytes=gpu_bytes,
        )
        return estimate.total + additional_ram_bytes

    stand_in = needed("bwa-mem3", aligner_gpu_bytes(aligner, ref, reads))
    budget = _gpu_budget(additional_ram_bytes)
    if tool == "bwa":
        limit = min(budget, available_memory())
        room = f"the {limit / 1024**3:.1f} GiB of RAM available"
    elif requested_aligner(job) == "auto":
        # Auto chose bwa-mem2 because it fits the RAM available now.
        limit = available_memory()
        room = f"the {limit / 1024**3:.1f} GiB of RAM available"
    else:
        limit = max(budget, needed(bwa_mem2_memory_model(job.native_aligners)))
        room = f"the {budget / 1024**3:.1f} GiB of RAM it can use"
    if stand_in > limit:
        logging.info(
            f"Not using {native.binary} in place of {tool}: the alignment is "
            f"estimated to need {stand_in / 1024**3:.1f} GiB with it, more than "
            f"{room}."
        )
        return None
    return aligner


def bwa_mem2_memory_model(native_aligners: str | None) -> str:
    """The memory model of bwa-mem2 for a run with this native_aligners
    setting: its Apple Silicon build's when that maps without the .0123 file
    (AlignerCommand.memory_model)."""
    native = find_native_aligner("bwa-mem2", native_aligners, probe=True)
    return AlignerCommand("bwa-mem2", native).memory_model


def bwa_mem2_index_ready(model: str, ref: str) -> bool:
    """Whether ``ref`` has every index file that the aligner of memory
    ``model`` (AlignerCommand.memory_model) reads: bwa-mem3 and wgse-bwa-mem2
    0.5.0 and later don't read the .0123 file."""
    if model in {"bwa-mem3", "wgse-bwa-mem2"}:
        return bwa_mem2.has_bwa_mem3_index(ref)
    return bwa_mem2.has_index(ref)


def aligner_gpu_bytes(aligner: AlignerCommand, ref: str, reads: int | None) -> int:
    """What the aligner's GPU backend (Metal or CUDA) needs beyond its CPU estimate."""
    if not aligner.gpu:
        return 0
    if aligner.engine == "bwa":
        return bwa_gpu_memory(ref, reads, dense_sa_file=aligner.dense_sa is not None)
    if aligner.engine == "bwa-mem3":
        cuda = aligner.native is not None and aligner.native.gpu == "cuda"
        return BWA_MEM3_CUDA_BYTES if cuda else BWA_MEM3_METAL_BYTES
    if aligner.engine == "minimap2":
        return MINIMAP2_CUDA_BYTES
    return 0


def bwa_mem3_command(job: AlignJob) -> AlignerCommand:
    """How to run --aligner bwa-mem3: wgse-bwa-mem3 (on Apple Silicon and
    Linux x86-64), else bwa-mem3 from PATH or its Pixi environment, writing
    bwa's records (--compat=bwa-mem) or, with --fast, its faster preset's."""
    options = (BWA_MEM3_FAST,) if job.fast else ("--compat=bwa-mem",)
    native = find_native_aligner("bwa-mem3", job.native_aligners)
    if native is not None:
        return AlignerCommand(
            "bwa-mem3", native, gpu=native.gpu is not None, options=options
        )
    failure = None
    if get_tool_path("bwa-mem3") is None or (failure := bwa_mem3_failure()):
        option = "--aligner bwa-mem3" if job.aligner == "bwa-mem3" else "--fast"
        otherwise = (
            "drop --fast"
            if option == "--fast"
            else "align with --aligner auto" + (" without --fast" if job.fast else "")
        )
        if failure:
            # #727: a build that crashes at startup would otherwise fail the
            # alignment pipeline with a bare signal.
            raise WGSExtractError(
                f"{option} needs bwa-mem3, and the installed one can't run: "
                f"{failure}. Install a bwa-mem3 that runs here, or {otherwise}."
            )
        if (
            native_setting(job.native_aligners) == "off"
            and "bwa-mem3" in platform_binaries()
            and shutil.which("wgse-bwa-mem3")
        ):
            raise WGSExtractError(
                f"{option} needs bwa-mem3: wgse-bwa-mem3 is installed, but "
                "native_aligners is off (use --native-aligners auto), and "
                f"bwa-mem3 isn't installed. Or {otherwise}."
            )
        install = pixi_install_command("bwa-mem3") or (
            "pixi install --locked -e bwa-mem3"
        )
        raise WGSExtractError(
            f"{option} needs wgse-bwa-mem3 (Apple Silicon, Linux x86-64) or bwa-mem3 "
            f"(Bioconda: `{install}`); for "
            f"wgse-bwa-mem3, {INSTALL_HINT}. Or {otherwise}."
        )
    return AlignerCommand("bwa-mem3", options=options)


def require_bwa_mem3_index(ref: str) -> None:
    """bwa-mem3 maps with bwa-mem2's index, which it doesn't build here."""
    if not bwa_mem2.has_bwa_mem3_index(ref):
        missing = ", ".join(
            s for s in bwa_mem2.BWA_MEM3_INDEX_SUFFIXES if not Path(ref + s).is_file()
        )
        raise UsageError(
            f"bwa-mem3 maps with bwa-mem2's index, and {ref} has none "
            f"({missing or 'its build is incomplete'}). Make it with "
            f"`wgsextract ref index --aligner bwa-mem2 --ref {ref}` (or "
            f"`wgse-bwa-mem3 index {ref}`), or align with --aligner bwa."
        )


def aligned_output_path(
    outdir: str, r1: str, r2: str | None, fmt: str, aligner: str | None = None
) -> str:
    """Where alignment of r1 (and r2) writes: <stem>_aligned.bam or .cram
    (pbmm2 always writes BAM)."""
    stem = reads_stem(r1)
    if r2:
        stem = pair_stem(stem)
    ext = ".cram" if fmt == "CRAM" and aligner != "pbmm2" else ".bam"
    return str(absolute_path(Path(outdir, f"{stem}_aligned{ext}")))


class _AlignSetup(NamedTuple):
    threads: str
    memory: str
    outdir: str
    ref: str
    stem: str
    r1: str
    alignment_threads: str | None = None
    compression_threads: str | None = None


def _require_unambiguous_library(ref: str | None, task: str) -> None:
    """FASTQs don't say which genome they belong to, so a reference library
    holding several can't be picked from silently (it would be the first
    known name, e.g. hg19 for hg38 reads)."""
    if ref and Path(ref).is_file():
        return
    root = ReferenceLibrary(ref, skip_full_search=True).root
    if not root or not Path(root).is_dir():
        return
    fastas = sorted(
        str(Path(d, name))
        for d in (root, reference_genomes_dir(root))
        if Path(d).is_dir()
        for name in os.listdir(d)
        if is_fasta_path(name)
    )
    if len(fastas) > 1:
        listed = "\n  ".join(fastas)
        raise UsageError(
            f"{task} needs the genome FASTA to align to: pass --ref <FASTA>. "
            f"The reference library has {len(fastas)}:\n  {listed}"
        )


def _prepare_alignment(
    job: AlignJob,
    tools: list[str],
    task: str,
    *,
    read_paths: list[str] | None = None,
    samtools_sort: bool = True,
    validate_reads: bool = True,
) -> _AlignSetup:
    """With ``samtools_sort`` (the output is sorted by samtools, not the
    aligner), the setup's memory is the sort's per-thread memory: sort_memory
    of --memory for these reads."""
    verify_dependencies(tools)
    validate_output_profile(job.output_profile or "default")
    log_dependency_info(tools)
    threads, memory = get_resource_defaults(job.threads, job.memory)

    if not job.r1:
        raise UsageError("--r1 is required unless --genome resolves read inputs.")
    paths_to_check = (
        {f"read input {index + 1}": path for index, path in enumerate(read_paths)}
        if read_paths is not None
        else {"--r1": job.r1}
    )
    if read_paths is None and job.r2:
        paths_to_check["--r2"] = job.r2
    # realign streams a remote BAM/CRAM's reads (core.remote_input).
    verify_paths_exist(
        {label: path for label, path in paths_to_check.items() if not is_url(path)}
    )
    if samtools_sort:
        memory = sort_memory(
            threads, memory, input_bytes=regular_files_bytes(paths_to_check.values())
        )

    input_path = job.input or job.r1
    outdir = job.outdir or str(absolute_path(input_path).parent)
    Path(outdir).mkdir(parents=True, exist_ok=True)
    logging.debug(f"Input file: {str(absolute_path(input_path))}")
    logging.debug(f"Output directory: {str(absolute_path(outdir))}")

    md5_sig = calculate_bam_md5(input_path, None) if job.input else None
    if not md5_sig:
        _require_unambiguous_library(job.ref, task)
    resolved_ref = resolve_reference(job.ref, md5_sig)
    if not resolved_ref or not Path(resolved_ref).is_file():
        raise UsageError(LOG_MESSAGES["ref_required_for"].format(task=task))
    logging.info(f"Aligning to {resolved_ref}")

    stem = reads_stem(job.r1)
    if job.r2:
        # Both mates go into one output, so it shouldn't be named after R1.
        stem = pair_stem(stem)
    setup = _AlignSetup(
        threads,
        memory,
        outdir,
        resolved_ref,
        stem,
        job.r1,
        str(stage_thread_plan(job.threads).alignment),
        str(stage_thread_plan(job.threads).compression),
    )
    return _validated_read_setup(job, setup) if validate_reads else setup


def _validated_read_setup(job: AlignJob, setup: _AlignSetup) -> _AlignSetup:
    if job.read_cleanup is None or _is_alignment_file(setup.r1):
        return setup
    for field in ("r1", "r2"):
        path = getattr(job, field)
        if path:
            staged = stage_read_input(
                path, configured_temp_directory() or setup.outdir, job.read_cleanup
            )
            setattr(job, field, staged)
    if job.r1 and fastq_input(job.r1):
        logging.info("Validating complete FASTQ input and exact paired read counts...")
        statistics = validate_fastq(job.r1, job.r2)
        job.input_reads = InputReads(*statistics)
        job.exact_reads = statistics.reads
    return setup._replace(r1=job.r1 or setup.r1)


def ensure_bwa_index(ref: str) -> None:
    from wgsextract_cli.core.bwa_index import ensure_index

    try:
        ensure_index(ref, runner=run_command)
    except (OSError, subprocess.SubprocessError) as error:
        raise WGSExtractError(f"Automatic BWA indexing failed: {error}") from error


def ensure_fasta_index(ref: str) -> None:
    if not Path(ref + ".fai").is_file():
        run_command(["samtools", "faidx", ref])


def alt_contigs(ref: str) -> list[str]:
    """The reference's ALT haplotype contigs (GRCh38's *_alt, and the HLA alleles
    in hs38DH), which repeat stretches of the primary chromosomes."""
    fai = Path(ref + ".fai")
    ensure_fasta_index(ref)
    if not fai.is_file():
        return []
    names = [line.split("\t", 1)[0] for line in fai.read_text().splitlines()]
    return [n for n in names if n.endswith("_alt") or n.startswith("HLA-")]


def ensure_bwa_alt_file(ref: str) -> None:
    """Make BWA ALT-aware on a reference with ALT contigs. Without <ref>.alt,
    a read from a region that has an ALT copy aligns equally well to both and
    gets mapping quality 0, which variant callers count as no evidence: such
    regions lose their variants. BWA reads only the contig names from the file
    (an existing bwakit .alt, which also has their alignments, is kept)."""
    alt_file = Path(ref + ".alt")
    if alt_file.exists():
        return
    names = alt_contigs(ref)
    if not names:
        return
    try:
        alt_file.write_text("".join(f"{n}\n" for n in names), newline="\n")
    except OSError as e:
        raise WGSExtractError(
            f"{ref} has {len(names)} ALT contigs, and BWA needs {alt_file} listing "
            f"them to give reads in those regions a mapping quality: {e}"
        ) from e
    logging.info(f"Wrote {alt_file}: BWA maps ALT-aware over {len(names)} contigs.")


def duplicate_marker(paired: bool) -> str:
    """How short reads get their duplicates marked: samblaster in the stream
    when it's installed (it aborts on unpaired input), else samtools markdup,
    which samtools always provides."""
    if paired and get_tool_path("samblaster") is not None:
        return "samblaster"
    return "markdup"


def _bwa_setup(
    job: AlignJob,
    task: str,
    *,
    read_paths: list[str] | None = None,
    additional_ram_bytes: int = 0,
) -> tuple[_AlignSetup, AlignerCommand]:
    """The alignment setup and how to run bwa, bwa-mem2 (short_read_aligner's
    choice, or an Apple Silicon build in its place) or bwa-mem3, with the
    reference's index and ALT contig list in place."""
    tool = short_read_tool(job)
    tools = ["samtools"] if tool == "bwa-mem3" else [tool, "samtools"]
    setup = _prepare_alignment(
        job, tools, task, read_paths=read_paths, validate_reads=False
    )
    mem3 = None
    if tool == "bwa-mem3":
        mem3 = bwa_mem3_command(job)
        require_bwa_mem3_index(setup.ref)
    setup = _validated_read_setup(job, setup)
    samblaster = bool(job.r2) and get_tool_path("samblaster") is not None
    if job.input_reads is not None:
        reads, read_length = job.input_reads
    elif setup.r1.lower().endswith((".bam", ".cram")):
        reads = indexed_read_count(setup.r1, setup.threads)
        read_length = None
    else:
        statistics = fastq_input_statistics(setup.r1, job.r2)
        reads = statistics.reads if statistics is not None else None
        read_length = statistics.read_length if statistics is not None else None
    if mem3 is not None:
        aligner = mem3
        dense_suffix_array.other_aligner(
            aligner.binary, enabled=bool(job.dense_suffix_array)
        )
        aligner.announce()
    else:
        aligner, memory = short_read_command(
            job,
            setup.ref,
            threads=setup.threads,
            memory=setup.memory,
            samblaster=samblaster,
            reads=reads,
            read_length=read_length,
            additional_ram_bytes=additional_ram_bytes,
        )
        setup = setup._replace(memory=memory)
    plan = stage_thread_plan(job.threads, metal=aligner.gpu)
    plan.record()
    setup = setup._replace(alignment_threads=str(plan.alignment))
    # The memory model of the aligner that runs (bwa-mem3 in another's place,
    # or wgse-bwa-mem2 without the .0123 file).
    engine = aligner.engine
    model = aligner.memory_model
    gpu_bytes = aligner_gpu_bytes(aligner, setup.ref, reads)
    if additional_ram_bytes and not streaming_mapping_fits(
        setup.ref,
        model,
        threads=setup.threads,
        alignment_threads=setup.alignment_threads,
        memory=setup.memory,
        samblaster=samblaster,
        reads=reads,
        platform=job.platform,
        read_length=read_length,
        additional_ram_bytes=additional_ram_bytes,
        gpu_bytes=gpu_bytes,
        dense_sa_bytes=aligner.dense_sa_bytes,
        staged=aligner.staged,
    ):
        raise StreamingMemoryUnavailable("Collation exceeds available alignment RAM")
    preflight_resources(
        lambda: estimate_resources(
            "align",
            input_paths=read_paths
            if read_paths is not None
            else [path for path in (job.r1, job.r2) if path],
            output_dir=setup.outdir,
            temp_dir=configured_temp_directory(),
            reference=setup.ref,
            threads=setup.threads,
            alignment_threads=setup.alignment_threads,
            memory=setup.memory,
            aligner=model,
            samblaster=samblaster,
            reads=reads,
            platform=job.platform,
            read_length=read_length,
            additional_ram_bytes=additional_ram_bytes,
            gpu_bytes=gpu_bytes,
            dense_sa_bytes=aligner.dense_sa_bytes,
            staged=aligner.staged,
            retire_fastqs=job.retire_fastqs,
            staged_workspace=(
                str(
                    workspace_path(
                        aligned_output_path(setup.outdir, setup.r1, job.r2, job.format)
                    )
                )
                if aligner.staged
                else None
            ),
            output_format=job.format,
            duplicate_marking=(
                "gatk-flow"
                if job.platform == "ultima" and get_tool_path("gatk") is not None
                else None
            ),
        )
    )
    try:
        check_mapping_memory(
            setup.ref,
            model,
            threads=setup.threads,
            alignment_threads=setup.alignment_threads,
            memory=setup.memory,
            samblaster=samblaster,
            reads=reads,
            platform=job.platform,
            read_length=read_length,
            additional_ram_bytes=additional_ram_bytes,
            gpu_bytes=gpu_bytes,
            dense_sa_bytes=aligner.dense_sa_bytes,
            staged=aligner.staged,
            required=requested_aligner(job) == "bwa-mem2",
            label=stand_in_label(aligner),
        )
    except WGSExtractError as error:
        if additional_ram_bytes:
            raise StreamingMemoryUnavailable(str(error)) from error
        raise
    warn_alignment_sort_space(configured_temp_directory() or setup.outdir, reads)
    job.input_reads = InputReads(reads, read_length)
    if engine == "bwa-mem2" and not bwa_mem2_index_ready(
        aligner.memory_model, setup.ref
    ):
        bwa_mem2.ensure_index(setup.ref, download=False)
    elif engine == "bwa":
        ensure_bwa_index(setup.ref)
    ensure_bwa_alt_file(setup.ref)
    print_warning("ButtonBWAAlign", threads=setup.alignment_threads or setup.threads)
    return setup, aligner


def warn_bwa_mem3_fast(binary: str) -> None:
    warn(
        "bwa_mem3_fast",
        f"Aligned with {binary} --fast: bwa-mem3's speed preset trades "
        "alignments for speed, so this output differs from BWA's. Drop --fast "
        "for BWA's exact alignments.",
        aligner=binary,
    )


def align_bwa(
    job: AlignJob,
    *,
    upstream: Sequence[Sequence[object]] = (),
    read_paths: list[str] | None = None,
    additional_ram_bytes: int = 0,
) -> None:
    """BWA-MEM alignment with bwa or bwa-mem2 (short_read_aligner's choice),
    or bwa-mem3."""
    setup, aligner = _bwa_setup(
        job,
        "BWA alignment",
        read_paths=read_paths,
        additional_ram_bytes=additional_ram_bytes,
    )

    ext = ".cram" if job.format == "CRAM" else ".bam"
    out_bam = str(Path(setup.outdir, f"{setup.stem}_aligned{ext}"))

    logging.info(
        LOG_MESSAGES["aligning_reads"].format(
            input=read_paths[0] if read_paths else job.r1,
            output=out_bam,
            tool=aligner.binary,
        )
    )
    tool = aligner.tool
    aligner.record()
    if aligner.bwa_mem3_mode == "fast":
        warn_bwa_mem3_fast(aligner.binary)
    align_cmd = [
        *aligner.mem_argv(), "-K", BWA_BATCH_BASES, "-t", setup.alignment_threads or setup.threads,
        "-R", read_group(setup.stem, job.sample, job.platform),
    ]  # fmt: skip
    align_cmd += [setup.ref, setup.r1]
    try:
        command = (
            [*align_cmd[:-2], "-p", setup.ref, "-"]
            if upstream
            else align_cmd + ([job.r2] if job.r2 else [])
        )
        run_streamed_alignment(
            command,
            out_bam,
            threads=setup.threads,
            memory=setup.memory,
            fmt=job.format,
            output_profile=job.output_profile or "default",
            ref=setup.ref,
            duplicates=duplicate_marker(paired=bool(job.r2)),
            upstream=list(upstream) if upstream else None,
            expected_reads=job.read_count,
            exact_reads=job.exact_reads,
            staged_mode=aligner.staged,
            compression_threads=setup.compression_threads,
            retire_fastqs=job.retire_fastqs,
            read_length=job.input_reads.read_length if job.input_reads else None,
        )
    except (OSError, subprocess.SubprocessError, WGSExtractError) as e:
        raise WGSExtractError(f"{aligner.binary} alignment failed: {e}") from e
    record_alignment(out_bam, tool, setup.ref)


# Ultima's duplicate marking (Picard MarkDuplicates in flow mode, as in Ultima's
# germline pipeline): single-end reads are duplicates only when both ends
# match, a read trimmed at the adapter (its tm string has an A) having a known end.
ULTIMA_DUPLICATE_ARGS = [
    "--FLOW_MODE", "true",
    "--FLOW_USE_END_IN_UNPAIRED_READS", "true",
    "--FLOW_USE_UNPAIRED_CLIPPED_END", "true",
    "--FLOW_UNPAIRED_END_UNCERTAINTY", "0",
]  # fmt: skip


def align_ultima(job: AlignJob, cram_opt: list[str] | None = None) -> None:
    """Align Ultima reads (FASTQ, or a BAM/CRAM: unaligned from the sequencer,
    or aligned, to realign) with BWA-MEM as Ultima's pipeline does. The flow
    tags travel through the FASTQ comment (-C) in each read's own orientation;
    -Y soft-clips supplementary alignments, so every record keeps the whole
    read its tp/t0 arrays describe. Duplicates are marked in flow mode."""
    require_native_tool("gatk")
    setup, aligner = _bwa_setup(job, "Ultima alignment")
    tool = aligner.tool
    reads = setup.r1
    ext = ".cram" if job.format == "CRAM" else ".bam"
    out_path = str(Path(setup.outdir, f"{setup.stem}_aligned{ext}"))
    logging.info(
        LOG_MESSAGES["aligning_reads"].format(
            input=reads, output=out_path, tool=aligner.binary
        )
    )
    aligner.record()
    bwa = [
        *aligner.mem_argv(),
        "-K",
        BWA_BATCH_BASES,
        "-t",
        setup.alignment_threads or setup.threads,
        "-Y",
    ]
    with tempfile.TemporaryDirectory(
        dir=configured_temp_directory() or setup.outdir, prefix=".wgse_ug_"
    ) as tmp:
        stages: list[Sequence[object]] = []
        if reads.lower().endswith((".bam", ".cram")):
            header = get_bam_header(reads, cram_opt)
            tagged = True
            rg_lines = ultima_read_groups(header, job.sample, setup.stem)
            tags = [*ultima.READ_TAGS, *(["RG"] if rg_lines else [])]
            stages += [
                ["samtools", "view", "-h", "--no-PG", "-F", "0x900"]
                + [*(cram_opt or []), reads],
                ultima.flow_tag_filter(),
                ["samtools", "fastq", "-n", "-T", ",".join(tags), "-"],
            ]
            if rg_lines:
                rg_file = Path(tmp, "read_groups.sam")
                rg_file.write_text("\n".join(rg_lines) + "\n", newline="\n")
                bwa += ["-H", str(rg_file)]
            else:
                bwa += ["-R", _ultima_read_group(job, setup.stem, tagged)]
            bwa += ["-C", setup.ref, "-"]
        else:
            tagged = ultima.fastq_has_flow_tags(reads)
            bwa += ["-R", _ultima_read_group(job, setup.stem, tagged)]
            bwa += [*(["-C"] if tagged else []), setup.ref, reads]
        if not tagged:
            logging.info(
                f"{reads} has no flow tags (tp, t0): variant callers will treat "
                "its alignment as ordinary short reads."
            )
        stages.append(bwa)
        if tagged:
            stages.append(ultima.flow_tag_filter())
        try:
            _sort_and_mark_ultima(
                stages,
                out_path,
                setup,
                job.format,
                tmp,
                output_profile=job.output_profile or "default",
                expected_reads=job.read_count,
                exact_reads=job.exact_reads,
            )
        except (OSError, subprocess.SubprocessError, WGSExtractError) as e:
            raise WGSExtractError(f"{aligner.binary} alignment failed: {e}") from e
    record_alignment(out_path, tool, setup.ref)


def _sort_and_mark_ultima(
    stages: list[Sequence[object]],
    out_path: str,
    setup: _AlignSetup,
    fmt: str,
    tmp: str,
    *,
    expected_reads: int | None = None,
    exact_reads: int | None = None,
    output_profile: str = "default",
) -> None:
    """Sort the aligner's output, mark duplicates with Picard's flow mode (GATK),
    or samtools markdup when GATK is missing, then index."""
    require_native_tool("gatk")
    if get_tool_path("gatk") is None:
        logging.warning(
            "GATK isn't installed, so duplicates are marked with samtools markdup, "
            "by where reads start only. Ultima's flow mode also needs their ends "
            "to match; install GATK (see `wgsextract deps`) for it."
        )
        run_streamed_alignment(
            stages[-1],
            out_path,
            threads=setup.threads,
            memory=setup.memory,
            fmt=fmt,
            ref=setup.ref,
            duplicates="markdup",
            upstream=stages[:-1],
            expected_reads=expected_reads,
            exact_reads=exact_reads,
            output_profile=output_profile,
        )
        return
    sorted_bam = str(Path(tmp, "sorted.bam"))
    metrics = str(Path(setup.outdir, f"{setup.stem}_duplicate_metrics.txt"))
    sort = get_sam_sort_cmd(
        sorted_bam, setup.threads, setup.memory, sam_input=True, temp_dir=tmp
    )
    partial = [out_path, out_path + ".bai", out_path + ".crai"]
    with cleanup_files(on_failure=partial, preserve_outputs=atomic_outputs_enabled()):
        with safe_output(out_path) as staged:
            marked = staged if fmt == "BAM" else str(Path(tmp, "marked.bam"))
            run_pipeline(
                [*stages, sort],
                description="Alignment",
                heartbeat=True,
                expected_reads=expected_reads,
            )
            logging.info("Marking duplicates in flow mode (GATK MarkDuplicates)...")
            run_command(
                [
                    "gatk",
                    "MarkDuplicates",
                    "-I",
                    java_local_path(sorted_bam),
                    "-O",
                    java_local_path(marked),
                    "-M",
                    java_local_path(metrics),
                    "--TMP_DIR",
                    java_local_path(tmp),
                    "--VALIDATION_STRINGENCY",
                    "SILENT",
                ]
                + ULTIMA_DUPLICATE_ARGS
            )
            Path(sorted_bam).unlink(missing_ok=True)
            if fmt == "CRAM":
                run_command(
                    [
                        "samtools",
                        "view",
                        "-C",
                        "-@",
                        setup.compression_threads or setup.threads,
                        "-T",
                        setup.ref,
                    ]
                    + output_profile_options(output_profile)
                    + ["-o", staged, marked]
                )
            if exact_reads is not None:
                check_primary_reads(staged, exact_reads, setup.ref)
        logging.info(LOG_MESSAGES["indexing_output"])
        _index_output(out_path, threads=setup.threads)


def _run_gpu(job: AlignJob, aligner: str) -> None:
    """The same alignment on an NVIDIA GPU: Parabricks fq2bam for BWA, its
    minimap2 for HiFi and ONT reads (map-pbmm2 standing in for pbmm2)."""
    if aligner == "bwa":
        _align_gpu_bwa(job)
        return
    if aligner == "pbmm2":
        if (job.preset or ("subread" if job.platform == "clr" else "ccs")) not in (
            "ccs",
            "map-hifi",
        ):
            raise UsageError("--gpu aligns PacBio HiFi/CCS reads, not CLR/subreads.")
        _align_gpu_minimap2(job, "map-pbmm2")
        return
    preset = _minimap2_preset(job)
    if preset not in _GPU_MINIMAP2_PRESETS:
        raise UsageError(
            f"--gpu has no minimap2 preset for {preset}: it aligns HiFi (map-hifi) "
            "and ONT (map-ont) long reads, and short reads with --aligner bwa."
        )
    _align_gpu_minimap2(job, _GPU_MINIMAP2_PRESETS[preset])


def _align_gpu_bwa(job: AlignJob) -> None:
    """BWA-MEM, sorting and duplicate marking on the GPU (pbrun fq2bam), with
    the same index, ALT contigs and read group as align_bwa."""
    from wgsextract_cli.core.parabricks import host_path, verify_parabricks

    setup = _prepare_alignment(
        job, ["samtools"], "GPU alignment", samtools_sort=False, validate_reads=False
    )
    verify_parabricks()
    fit = _gpu_memory_options(job, "fq2bam")
    setup = _validated_read_setup(job, setup)
    ensure_bwa_index(setup.ref)
    ensure_bwa_alt_file(setup.ref)
    # fq2bam requires a PU (platform unit) in the read group.
    rg = read_group(setup.stem, job.sample, job.platform) + f"\\tPU:{setup.stem}"
    reads = [host_path(setup.r1)] + ([host_path(job.r2)] if job.r2 else [])
    fq_arg = "--in-fq" if job.r2 else "--in-se-fq"
    _gpu_align(job, setup, "fq2bam", [fq_arg, *reads, rg, *fit], reads)


def _align_gpu_minimap2(job: AlignJob, preset: str) -> None:
    """Long reads (FASTQ or unaligned BAM) with Parabricks minimap2."""
    from wgsextract_cli.core.parabricks import host_path, verify_parabricks

    if job.r2:
        raise UsageError("Long-read alignment takes single-end input; drop --r2.")
    setup = _prepare_alignment(
        job,
        ["samtools"],
        "GPU long-read alignment",
        samtools_sort=False,
        validate_reads=False,
    )
    verify_parabricks()
    fit = _gpu_memory_options(job, "minimap2")
    setup = _validated_read_setup(job, setup)
    reads = host_path(setup.r1)
    in_arg = "--in-bam" if reads.lower().endswith(".bam") else "--in-fq"
    sample = job.sample or pair_stem(setup.stem)
    platform = _RG_PLATFORMS.get(job.platform or "", "PACBIO")
    _gpu_align(
        job,
        setup,
        "minimap2",
        [in_arg, reads, "--preset", preset]
        + ["--read-group-sm", sample, "--read-group-lb", sample]
        + ["--read-group-pl", platform, "--read-group-id-prefix", setup.stem]
        + fit,
        [reads],
    )
    # From an unaligned BAM, Parabricks keeps the BAM's own read groups, which
    # PacBio writes without the sample name callers need.
    _ensure_read_group_sample(_gpu_output_path(job, setup), sample, setup.ref)


def _gpu_memory_options(job: AlignJob, tool: str) -> list[str]:
    """Check the GPUs' and the host's memory for Parabricks ``tool``, and the
    options that fit it to them."""
    from wgsextract_cli.core.parabricks import plan_gpu_memory

    return plan_gpu_memory(tool, "align", check=not job.no_gpu_memory_check)


def _gpu_output_path(job: AlignJob, setup: _AlignSetup) -> str:
    from wgsextract_cli.core.parabricks import host_path

    ext = ".cram" if job.format == "CRAM" else ".bam"
    return host_path(str(Path(setup.outdir, f"{setup.stem}_aligned{ext}")))


def _gpu_align(
    job: AlignJob,
    setup: _AlignSetup,
    tool: str,
    tool_args: list[str],
    reads: list[str],
) -> None:
    """Run a Parabricks aligner into <stem>_aligned.bam/.cram. Parabricks can't
    write CRAM against a gzipped reference (it crashes), so then it writes BAM
    and samtools converts it."""
    from wgsextract_cli.core.parabricks import host_path, run_pbrun

    out_path = _gpu_output_path(job, setup)
    convert = out_path.endswith(".cram") and (
        setup.ref.lower().endswith(".gz") or job.output_profile not in (None, "default")
    )
    pb_out = out_path.removesuffix(".cram") + ".bam" if convert else out_path
    logging.info(
        LOG_MESSAGES["aligning_reads"].format(
            input=job.r1, output=out_path, tool=f"Parabricks {tool}"
        )
    )
    with (
        cleanup_files(
            on_failure=[out_path, pb_out, pb_out + ".bai"],
            preserve_outputs=atomic_outputs_enabled(),
        ),
        tempfile.TemporaryDirectory(
            dir=configured_temp_directory() or setup.outdir, prefix=".wgse_pb_"
        ) as tmp,
    ):
        with safe_output(out_path) as staged:
            if atomic_outputs_enabled():
                pb_out = str(Path(tmp, "aligned.bam")) if convert else staged
            run_pbrun(
                tool,
                ["--ref", host_path(setup.ref), *tool_args]
                + ["--out-bam", pb_out, "--tmp-dir", host_path(tmp)],
                [setup.ref, *reads, pb_out, tmp],
            )
            if convert:
                run_command(
                    [
                        "samtools",
                        "view",
                        "-C",
                        "-@",
                        setup.compression_threads or setup.threads,
                        "-T",
                        setup.ref,
                    ]
                    + output_profile_options(job.output_profile or "default")
                    + ["-o", staged, pb_out]
                )
            if job.exact_reads is not None:
                check_primary_reads(staged, job.exact_reads, setup.ref)
            Path(pb_out.rsplit(".", 1)[0] + "_chrs.txt").unlink(missing_ok=True)
        if convert or atomic_outputs_enabled():
            _index_output(out_path, threads=setup.threads)
        if convert:
            for path in (pb_out, pb_out + ".bai"):
                Path(path).unlink(missing_ok=True)
    # Parabricks also lists the output's chromosomes beside it.
    Path(pb_out.rsplit(".", 1)[0] + "_chrs.txt").unlink(missing_ok=True)
    record_alignment(out_path, f"parabricks {tool}", setup.ref)


def _minimap2_command(
    job: AlignJob,
    ref: str,
    preset: str,
    *,
    threads: str,
    memory: str,
    samblaster: bool,
    reads: int | None,
    read_length: float | None,
    input_bytes: int | None,
) -> AlignerCommand:
    """minimap2, or wgse-minimap2 in its place (find_native_aligner). Long
    reads' gap fills run on its NVIDIA GPU (--backend auto, from wgse-minimap2
    0.4.0 on Linux) unless the GPU's extra RAM alone would make the pipeline
    exceed the RAM available; sr keeps them on the CPU."""
    native = find_native_aligner("minimap2", job.native_aligners)
    aligner = AlignerCommand("minimap2", native)
    if native is None or not native.minimap2_gpu or preset == "sr":
        return aligner
    gpu = AlignerCommand("minimap2", native, gpu=True)
    gpu_bytes = aligner_gpu_bytes(gpu, ref, reads)

    def needed(gpu_bytes: int) -> int:
        return estimate_mapping_memory(
            ref,
            "minimap2",
            threads=threads,
            alignment_threads=str(
                stage_thread_plan(job.threads, metal=bool(gpu_bytes)).alignment
            ),
            memory=memory,
            samblaster=samblaster,
            reads=reads,
            platform=job.platform,
            read_length=read_length,
            preset=preset,
            input_bytes=input_bytes,
            gpu_bytes=gpu_bytes,
        ).total

    # Long reads' estimates add headroom to the GPU's RAM too: compare both.
    with_gpu, cpu_only = needed(gpu_bytes), needed(0)
    available = available_memory()
    if with_gpu > available >= cpu_only:
        logging.info(
            f"Running {native.binary} on the CPU: its GPU backend needs "
            f"{gpu_bytes / 1024**3:.1f} GiB more RAM, and the alignment would "
            f"then need {with_gpu / 1024**3:.1f} GiB, more than the "
            f"{available / 1024**3:.1f} GiB available."
        )
        return aligner
    return gpu


def align_minimap2(job: AlignJob) -> None:
    if is_long_read(job) and job.r2:
        raise UsageError("Long reads are single-end: drop --r2.")
    preset = _minimap2_preset(job)
    setup = _prepare_alignment(job, ["minimap2", "samtools"], "Minimap2 alignment")

    ext = ".cram" if job.format == "CRAM" else ".bam"
    out_bam = str(Path(setup.outdir, f"{setup.stem}_aligned{ext}"))

    logging.info(
        LOG_MESSAGES["aligning_reads"].format(
            input=job.r1, output=out_bam, tool="Minimap2"
        )
    )
    logging.info("Using minimap2 preset %s for %s reads.", preset, job.platform)
    duplicates = duplicate_marker(paired=bool(job.r2)) if preset == "sr" else None
    read_files = [path for path in (job.r1, job.r2) if path]
    # Pipes and devices report no size; only regular files bound the batches.
    input_bytes = regular_files_bytes(read_files)
    read_count = job.read_count
    if read_count is None and duplicates == "samblaster":
        statistics = fastq_input_statistics(setup.r1, job.r2)
        read_count = statistics.reads if statistics is not None else None
    read_length = job.input_reads.read_length if job.input_reads else None
    if read_length is None and preset != "sr" and input_bytes:
        read_length = _long_read_length(setup.r1, job.input_cram_opt or [])
    aligner = _minimap2_command(
        job,
        setup.ref,
        preset,
        threads=setup.threads,
        memory=setup.memory,
        samblaster=duplicates == "samblaster",
        reads=read_count,
        read_length=read_length,
        input_bytes=input_bytes,
    )
    plan = stage_thread_plan(job.threads, metal=aligner.gpu)
    plan.record()
    setup = setup._replace(alignment_threads=str(plan.alignment))
    gpu_bytes = aligner_gpu_bytes(aligner, setup.ref, read_count)
    # realign checked a chunked alignment's disk space, chunk by chunk.
    if not job.chunks:
        preflight_resources(
            lambda: estimate_resources(
                "align",
                input_paths=read_files,
                output_dir=setup.outdir,
                temp_dir=configured_temp_directory(),
                reference=setup.ref,
                threads=setup.threads,
                alignment_threads=setup.alignment_threads,
                memory=setup.memory,
                aligner="minimap2",
                preset=preset,
                samblaster=duplicates == "samblaster",
                reads=read_count,
                platform=job.platform,
                read_length=read_length,
                output_format=job.format,
                gpu_bytes=gpu_bytes,
            )
        )
    check_mapping_memory(
        setup.ref,
        "minimap2",
        threads=setup.threads,
        alignment_threads=setup.alignment_threads,
        memory=setup.memory,
        samblaster=duplicates == "samblaster",
        reads=read_count,
        platform=job.platform,
        read_length=read_length,
        preset=preset,
        input_bytes=input_bytes,
        gpu_bytes=gpu_bytes,
    )
    aligner.announce()
    aligner.record()
    align_cmd = [*aligner.argv(), "-ax", preset, "-t", str(plan.alignment)]
    upstream: list[Sequence[object]] = []
    reads = setup.r1
    # realign --keep-fastq aligns a FASTQ, so it passes its BAM/CRAM's.
    description = job.read_group_description
    if is_long_read(job):
        if job.r2:
            raise UsageError("Long reads are single-end: drop --r2.")
        # Soft clipping keeps SEQ and the modification coordinates consistent.
        align_cmd += ["-Y"]
        if _is_alignment_file(reads):
            upstream = [long_read_fastq_command(reads, job.input_cram_opt or [])]
            description = read_group_descriptions(reads, job.input_ref)
        elif not is_fasta_path(reads):
            # FASTQ comments can mix ONT runid/ch metadata with SAM tags;
            # minimap2 -y alone would emit invalid SAM for those comments.
            upstream = [
                ["samtools", "import", "-u", "-T", LONG_READ_TAGS, "-0", reads],
                ["samtools", "fastq", "-n", "-T", LONG_READ_TAGS, "-"],
            ]
        # FASTA has no qualities (samtools would invent them) and no tags.
        if upstream:
            align_cmd += ["-y"]
            reads = "-"
    align_cmd += [
        "-R",
        read_group(setup.stem, job.sample, job.platform, description),
    ]
    if job.chunks and upstream and _is_alignment_file(setup.r1):
        try:
            ChunkedAlignment(
                input=job.chunk_input or setup.r1,
                output=out_bam,
                chunks=job.chunks,
                reference=setup.ref,
                format=job.format,
                output_profile=job.output_profile or "default",
                threads=setup.threads,
                memory=setup.memory,
                mapper=align_cmd,
                compression_threads=setup.compression_threads,
                # Building the index uses no GPU.
                indexer=[
                    *replace(aligner, gpu=False).argv(),
                    *(
                        "-x",
                        preset,
                        "-t",
                        str(stage_thread_plan(job.threads).alignment),
                    ),
                ],
                reads=["samtools", "fastq", "-n", "-T", LONG_READ_TAGS, "-"],
                decode=job.input_cram_opt or [],
                resume=job.resume_chunks,
            ).run()
            _index_output(out_bam, threads=setup.threads)
        except (OSError, subprocess.SubprocessError, WGSExtractError) as e:
            raise WGSExtractError(f"{aligner.binary} alignment failed: {e}") from e
        record_alignment(out_bam, "minimap2", setup.ref)
        return
    align_cmd += [setup.ref, reads]
    try:
        run_streamed_alignment(
            align_cmd + ([job.r2] if job.r2 else []),
            out_bam,
            threads=setup.threads,
            memory=setup.memory,
            fmt=job.format,
            output_profile=job.output_profile or "default",
            ref=setup.ref,
            duplicates=duplicates,
            upstream=upstream or None,
            exact_reads=job.exact_reads,
        )
    except (OSError, subprocess.SubprocessError, WGSExtractError) as e:
        raise WGSExtractError(f"{aligner.binary} alignment failed: {e}") from e
    record_alignment(out_bam, "minimap2", setup.ref)


def align_pbmm2(job: AlignJob) -> None:
    if configured_temp_directory() and not job.gpu:
        raise UsageError(
            "Native pbmm2's internal sorter does not support --tmp-dir. Use --aligner minimap2 or omit the temporary-directory setting."
        )
    if job.r2:
        raise UsageError(
            "PacBio alignment expects single-end BAM/FASTQ input; do not pass --r2."
        )
    if job.format == "CRAM":
        raise UsageError(
            "pbmm2 writes sorted BAM. Convert to CRAM with 'bam to-cram' after alignment."
        )
    preset_arg = job.preset or ("subread" if job.platform == "clr" else "ccs")
    if preset_arg not in _PBMM2_PRESETS:
        raise UsageError(
            "PacBio pbmm2 alignment supports --preset map-hifi, map-pb, ccs, or subread."
        )
    preset = _PBMM2_PRESETS[preset_arg]
    setup = _prepare_alignment(
        job, ["pbmm2", "samtools"], "PacBio alignment", samtools_sort=False
    )

    out_bam = str(Path(setup.outdir, f"{setup.stem}_aligned.bam"))
    sample = job.sample or setup.stem.split(".")[0]

    logging.info(
        LOG_MESSAGES["aligning_reads"].format(
            input=job.r1, output=out_bam, tool="pbmm2"
        )
    )
    cmd = ["pbmm2", "align", setup.ref, setup.r1, out_bam, "--sort"]
    cmd += [
        "--preset",
        preset,
        "--sample",
        sample,
        "-j",
        setup.alignment_threads or setup.threads,
    ]
    if preset == "SUBREAD" and job.exact_reads is None:
        cmd.append("--median-filter")
    if job.exact_reads is not None:
        cmd.append("--unmapped")
    if setup.r1.lower().endswith((".fastq", ".fastq.gz", ".fq", ".fq.gz")):
        cmd.extend(["--rg", f"@RG\\tID:{setup.stem}\\tSM:{sample}"])
    try:
        with safe_output(out_bam) as staged:
            cmd[4] = staged
            run_command(cmd)
            if job.exact_reads is not None:
                check_primary_reads(staged, job.exact_reads, setup.ref)
        logging.info(LOG_MESSAGES["indexing_output"])
        _index_output(out_bam, threads=setup.threads)
    except (OSError, subprocess.SubprocessError) as e:
        raise WGSExtractError(f"PacBio alignment failed: {e}") from e
    record_alignment(out_bam, "pbmm2", setup.ref)
