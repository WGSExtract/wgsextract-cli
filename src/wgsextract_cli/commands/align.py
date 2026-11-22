"""Align command orchestration with stable job/helper facade imports."""

from __future__ import annotations

import logging as logging
import os as os
import shutil as shutil
import subprocess as subprocess
import tempfile as tempfile
from collections.abc import Sequence as Sequence
from contextlib import ExitStack as ExitStack
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from pathlib import Path as Path
from typing import TYPE_CHECKING as TYPE_CHECKING
from typing import NamedTuple as NamedTuple
from typing import TypeVar as TypeVar

import psutil as psutil

from wgsextract_cli.api._params import job_values as job_values
from wgsextract_cli.api.align import ALIGNERS as ALIGNERS
from wgsextract_cli.api.align import PLATFORMS as PLATFORMS
from wgsextract_cli.api.align import AlignParams as AlignParams
from wgsextract_cli.cli.registration.align import register as register
from wgsextract_cli.core import bwa_mem2 as bwa_mem2
from wgsextract_cli.core import dense_suffix_array as dense_suffix_array
from wgsextract_cli.core import ultima as ultima
from wgsextract_cli.core.alignment.fastq import (
    check_primary_reads as check_primary_reads,
)
from wgsextract_cli.core.alignment.fastq import fastq_input as fastq_input
from wgsextract_cli.core.alignment.fastq import stage_read_input as stage_read_input
from wgsextract_cli.core.alignment.fastq import validate_fastq as validate_fastq
from wgsextract_cli.core.alignment.memory import (
    BWA_MEM3_CUDA_BYTES as BWA_MEM3_CUDA_BYTES,
)
from wgsextract_cli.core.alignment.memory import (
    BWA_MEM3_METAL_BYTES as BWA_MEM3_METAL_BYTES,
)
from wgsextract_cli.core.alignment.memory import (
    MINIMAP2_CUDA_BYTES as MINIMAP2_CUDA_BYTES,
)
from wgsextract_cli.core.alignment.memory import available_memory as available_memory
from wgsextract_cli.core.alignment.memory import bwa_gpu_memory as bwa_gpu_memory
from wgsextract_cli.core.alignment.memory import (
    check_mapping_memory as check_mapping_memory,
)
from wgsextract_cli.core.alignment.memory import (
    estimate_mapping_memory as estimate_mapping_memory,
)
from wgsextract_cli.core.alignment.metadata import get_bam_header as get_bam_header
from wgsextract_cli.core.alignment.profiles import (
    output_profile_options as output_profile_options,
)
from wgsextract_cli.core.alignment.profiles import (
    validate_output_profile as validate_output_profile,
)
from wgsextract_cli.core.alignment.read_counts import (
    fastq_input_statistics as fastq_input_statistics,
)
from wgsextract_cli.core.alignment.samtools_commands import parse_memory as parse_memory
from wgsextract_cli.core.alignment.samtools_commands import (
    regular_files_bytes as regular_files_bytes,
)
from wgsextract_cli.core.alignment.samtools_commands import sort_memory as sort_memory
from wgsextract_cli.core.atomic_output import atomic_output_mode as atomic_output_mode
from wgsextract_cli.core.atomic_output import (
    atomic_outputs_enabled as atomic_outputs_enabled,
)
from wgsextract_cli.core.atomic_output import safe_output as safe_output
from wgsextract_cli.core.chunked_alignment import Chunk as Chunk
from wgsextract_cli.core.chunked_alignment import ChunkedAlignment as ChunkedAlignment
from wgsextract_cli.core.config import settings as settings
from wgsextract_cli.core.config import typed_settings as typed_settings
from wgsextract_cli.core.dependencies import get_tool_path as get_tool_path
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info as log_dependency_info,
)
from wgsextract_cli.core.dependency_checks import (
    verify_dependencies as verify_dependencies,
)
from wgsextract_cli.core.genome_library import pair_stem as pair_stem
from wgsextract_cli.core.messages import LOG_MESSAGES as LOG_MESSAGES
from wgsextract_cli.core.native_aligners import BWA_MEM3_COMPAT as BWA_MEM3_COMPAT
from wgsextract_cli.core.native_aligners import BWA_MEM3_FAST as BWA_MEM3_FAST
from wgsextract_cli.core.native_aligners import INSTALL_HINT as INSTALL_HINT
from wgsextract_cli.core.native_aligners import AlignerCommand as AlignerCommand
from wgsextract_cli.core.native_aligners import NativeAligner as NativeAligner
from wgsextract_cli.core.native_aligners import (
    NativeMemoryDecision as NativeMemoryDecision,
)
from wgsextract_cli.core.native_aligners import bwa_mem3_failure as bwa_mem3_failure
from wgsextract_cli.core.native_aligners import find_bwa_mem3 as find_bwa_mem3
from wgsextract_cli.core.native_aligners import (
    find_native_aligner as find_native_aligner,
)
from wgsextract_cli.core.native_aligners import (
    native_gpu_preference as native_gpu_preference,
)
from wgsextract_cli.core.native_aligners import native_setting as native_setting
from wgsextract_cli.core.native_aligners import platform_binaries as platform_binaries
from wgsextract_cli.core.native_support import (
    require_native_tool as require_native_tool,
)
from wgsextract_cli.core.paths import absolute_path as absolute_path
from wgsextract_cli.core.paths import java_local_path as java_local_path
from wgsextract_cli.core.pixi_tools import pixi_install_command as pixi_install_command
from wgsextract_cli.core.ref_library.assets import is_fasta_path as is_fasta_path
from wgsextract_cli.core.ref_library.assets import (
    reference_genomes_dir as reference_genomes_dir,
)
from wgsextract_cli.core.ref_library.resolver import (
    ReferenceLibrary as ReferenceLibrary,
)
from wgsextract_cli.core.remote_input import is_url as is_url
from wgsextract_cli.core.resource_estimates import (
    estimate_resources as estimate_resources,
)
from wgsextract_cli.core.resource_estimates import (
    preflight_resources as preflight_resources,
)
from wgsextract_cli.core.resource_policy import stage_thread_plan as stage_thread_plan
from wgsextract_cli.core.results import current_result as current_result
from wgsextract_cli.core.results import warn as warn
from wgsextract_cli.core.staged_alignment import workspace_path as workspace_path
from wgsextract_cli.core.temp_directory import (
    configured_temp_directory as configured_temp_directory,
)
from wgsextract_cli.core.utils import UsageError as UsageError
from wgsextract_cli.core.utils import WGSExtractError as WGSExtractError
from wgsextract_cli.core.utils import get_resource_defaults as get_resource_defaults
from wgsextract_cli.core.utils import get_sam_sort_cmd as get_sam_sort_cmd
from wgsextract_cli.core.utils import run_command as run_command
from wgsextract_cli.core.utils import run_pipeline as run_pipeline
from wgsextract_cli.core.variant_files import calculate_bam_md5 as calculate_bam_md5
from wgsextract_cli.core.variant_files import resolve_reference as resolve_reference
from wgsextract_cli.core.variant_files import verify_paths_exist as verify_paths_exist
from wgsextract_cli.core.warnings import indexed_read_count as indexed_read_count
from wgsextract_cli.core.warnings import print_warning as print_warning
from wgsextract_cli.core.warnings import (
    warn_alignment_sort_space as warn_alignment_sort_space,
)

from ._extract_helpers import cleanup_files as cleanup_files
from .alignment.backends import (
    ULTIMA_DUPLICATE_ARGS as ULTIMA_DUPLICATE_ARGS,
)
from .alignment.backends import (
    _minimap2_command as _minimap2_command,
)
from .alignment.backends import (
    _sort_and_mark_ultima as _sort_and_mark_ultima,
)
from .alignment.backends import (
    align_minimap2 as align_minimap2,
)
from .alignment.backends import (
    align_pbmm2 as align_pbmm2,
)
from .alignment.backends import (
    align_ultima as align_ultima,
)
from .alignment.common import (
    BWA_BATCH_BASES as BWA_BATCH_BASES,
)
from .alignment.common import (
    AlignJob as AlignJob,
)
from .alignment.common import (
    InputReads as InputReads,
)
from .alignment.common import (
    StreamingMemoryUnavailable as StreamingMemoryUnavailable,
)
from .alignment.common import (
    _AlignSetup as _AlignSetup,
)
from .alignment.common import (
    _Job as _Job,
)
from .alignment.common import (
    aligned_output_path as aligned_output_path,
)
from .alignment.common import (
    alt_contigs as alt_contigs,
)
from .alignment.common import (
    duplicate_marker as duplicate_marker,
)
from .alignment.common import (
    ensure_bwa_alt_file as ensure_bwa_alt_file,
)
from .alignment.common import (
    ensure_bwa_index as ensure_bwa_index,
)
from .alignment.common import (
    ensure_fasta_index as ensure_fasta_index,
)
from .alignment.gpu import (
    _GPU_MINIMAP2_PRESETS as _GPU_MINIMAP2_PRESETS,
)
from .alignment.gpu import (
    _align_gpu_bwa as _align_gpu_bwa,
)
from .alignment.gpu import (
    _align_gpu_minimap2 as _align_gpu_minimap2,
)
from .alignment.gpu import (
    _gpu_align as _gpu_align,
)
from .alignment.gpu import (
    _gpu_memory_options as _gpu_memory_options,
)
from .alignment.gpu import (
    _gpu_output_path as _gpu_output_path,
)
from .alignment.gpu import (
    _run_gpu as _run_gpu,
)
from .alignment.output import _ensure_read_group_sample as _ensure_read_group_sample
from .alignment.output import _index_output as _index_output
from .alignment.output import _sort_and_mark_stages as _sort_and_mark_stages
from .alignment.output import record_alignment as record_alignment
from .alignment.output import run_streamed_alignment as run_streamed_alignment
from .alignment.preparation import (
    _bwa_setup as _bwa_setup,
)
from .alignment.preparation import (
    _prepare_alignment as _prepare_alignment,
)
from .alignment.preparation import (
    _require_unambiguous_library as _require_unambiguous_library,
)
from .alignment.preparation import (
    _validated_read_setup as _validated_read_setup,
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
from .alignment.short_reads import (
    _GPU_HEADROOM_BYTES as _GPU_HEADROOM_BYTES,
)
from .alignment.short_reads import (
    _GPU_SORT_MEMORY_STEPS_MIB as _GPU_SORT_MEMORY_STEPS_MIB,
)
from .alignment.short_reads import (
    _bwa_gpu_fit as _bwa_gpu_fit,
)
from .alignment.short_reads import (
    _gpu_budget as _gpu_budget,
)
from .alignment.short_reads import (
    _gpu_name as _gpu_name,
)
from .alignment.short_reads import (
    aligner_gpu_bytes as aligner_gpu_bytes,
)
from .alignment.short_reads import bwa_index_suffixes
from .alignment.short_reads import (
    bwa_mem2_index_ready as bwa_mem2_index_ready,
)
from .alignment.short_reads import (
    bwa_mem2_memory_model as bwa_mem2_memory_model,
)
from .alignment.short_reads import (
    bwa_mem3_build as bwa_mem3_build,
)
from .alignment.short_reads import (
    bwa_mem3_command as bwa_mem3_command,
)
from .alignment.short_reads import (
    bwa_mem3_stand_in as bwa_mem3_stand_in,
)
from .alignment.short_reads import (
    native_short_read_aligner as native_short_read_aligner,
)
from .alignment.short_reads import (
    require_bwa_mem3_index as require_bwa_mem3_index,
)
from .alignment.short_reads import (
    short_read_aligner as short_read_aligner,
)
from .alignment.short_reads import (
    short_read_command as short_read_command,
)
from .alignment.short_reads import (
    short_read_sort_memory as short_read_sort_memory,
)
from .alignment.short_reads import (
    stand_in_label as stand_in_label,
)
from .alignment.short_reads import (
    streaming_mapping_fits as streaming_mapping_fits,
)

if TYPE_CHECKING:
    pass


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
    discarded_fastq: Path | None = None,
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
    ref = Path(setup.ref)
    try:
        command = (
            [*align_cmd, "-p", ref, "-"]
            if upstream
            else [*align_cmd, ref, Path(setup.r1), *([Path(job.r2)] if job.r2 else [])]
        )
        run_streamed_alignment(
            command,
            out_bam,
            threads=setup.threads,
            memory=setup.sort_budget,
            fmt=job.format,
            output_profile=job.output_profile or "default",
            ref=setup.ref,
            duplicates=duplicate_marker(paired=bool(job.r2)),
            upstream=list(upstream) if upstream else None,
            discarded_fastq=discarded_fastq,
            expected_reads=job.read_count,
            exact_reads=job.exact_reads,
            staged_mode=aligner.staged,
            compression_threads=setup.compression_threads,
            retire_fastqs=job.retire_fastqs,
            read_length=job.input_reads.read_length if job.input_reads else None,
            dense_sa=aligner.dense_sa,
            index_suffixes=bwa_index_suffixes(aligner.memory_model)
            if aligner.staged
            else (),
        )
    except (OSError, subprocess.SubprocessError, WGSExtractError) as e:
        raise WGSExtractError(f"{aligner.binary} alignment failed: {e}") from e
    record_alignment(out_bam, tool, setup.ref)
