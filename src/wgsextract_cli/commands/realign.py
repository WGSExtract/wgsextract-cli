"""
(Re)align an existing BAM/CRAM to a reference genome.

Workflow:
  1. Resolve FASTQ inputs:
     - If ``--r1`` (and optionally ``--r2``) is provided, use those.
     - Otherwise look next to the input BAM and in the output directory for
       previously extracted FASTQ files (common naming patterns: ``<base>_R1.fastq.gz``, ``<base>.r1.fq.gz``,
       etc). If found, reuse them rather than re-extracting.
     - Otherwise stream paired FASTQ into BWA, or extract when files are needed.
  2. Host requirement: estimate disk space for both the extraction stage (if
     needed) and the alignment stage, RAM and time, print them in one line,
     and refuse to start without the disk or the least RAM the run needs
     (#690). This avoids the classic failure mode of running an alignment for
     hours only to crash at the sort stage because of disk pressure.
  3. Delegate to the existing align command.

Extraction (when FASTQs are written to files) and alignment are steps (see
core.steps): a rerun after a crash or kill skips an alignment or extraction
that finished, and redoes the one that didn't.
"""

import logging as logging
import math as math
import os as os
import shutil as shutil
import stat as stat
from collections.abc import Callable as Callable
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from pathlib import Path as Path
from typing import Any as Any
from typing import NamedTuple as NamedTuple
from typing import cast as cast
from urllib.parse import urlsplit as urlsplit

from wgsextract_cli.api.align import RealignParams as RealignParams
from wgsextract_cli.cli.registration.realign import register as register
from wgsextract_cli.core import bwa_mem2 as bwa_mem2
from wgsextract_cli.core import ultima as ultima
from wgsextract_cli.core.alignment.memory import available_memory as available_memory
from wgsextract_cli.core.alignment.memory import (
    check_mapping_memory as check_mapping_memory,
)
from wgsextract_cli.core.alignment.memory import (
    estimate_mapping_memory as estimate_mapping_memory,
)
from wgsextract_cli.core.alignment.memory import (
    least_mapping_memory as least_mapping_memory,
)
from wgsextract_cli.core.alignment.memory import reference_bases as reference_bases
from wgsextract_cli.core.alignment.metadata import (
    cram_encoded_with as cram_encoded_with,
)
from wgsextract_cli.core.alignment.metadata import get_bam_header as get_bam_header
from wgsextract_cli.core.alignment.profiles import (
    validate_output_profile as validate_output_profile,
)
from wgsextract_cli.core.alignment.read_counts import (
    alignment_sample as alignment_sample,
)
from wgsextract_cli.core.alignment.read_counts import (
    fastq_input_statistics as fastq_input_statistics,
)
from wgsextract_cli.core.alignment.read_counts import unpaired_sample as unpaired_sample
from wgsextract_cli.core.alignment.samtools_commands import parse_memory as parse_memory
from wgsextract_cli.core.alignment.samtools_commands import (
    regular_files_bytes as regular_files_bytes,
)
from wgsextract_cli.core.alignment.samtools_commands import sort_memory as sort_memory
from wgsextract_cli.core.alignment.sequencing import input_platform as input_platform
from wgsextract_cli.core.alignment.sequencing import pacbio_platform as pacbio_platform
from wgsextract_cli.core.alignment.sequencing import (
    record_platform_choice as record_platform_choice,
)
from wgsextract_cli.core.atomic_output import atomic_output as atomic_output
from wgsextract_cli.core.chunked_alignment import ChunkPlan as ChunkPlan
from wgsextract_cli.core.chunked_alignment import chunked_disk as chunked_disk
from wgsextract_cli.core.chunked_alignment import contigs as contigs
from wgsextract_cli.core.chunked_alignment import parse_size as parse_size
from wgsextract_cli.core.chunked_alignment import plan_chunks as plan_chunks
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info as log_dependency_info,
)
from wgsextract_cli.core.dependency_checks import (
    verify_dependencies as verify_dependencies,
)
from wgsextract_cli.core.native_aligners import AlignerCommand as AlignerCommand
from wgsextract_cli.core.native_aligners import (
    native_gpu_preference as native_gpu_preference,
)
from wgsextract_cli.core.paths import absolute_path as absolute_path
from wgsextract_cli.core.reference_assets import (
    find_fasta_by_signature as find_fasta_by_signature,
)
from wgsextract_cli.core.reference_assets import input_stem as input_stem
from wgsextract_cli.core.reference_assets import (
    reference_genomes_dir as reference_genomes_dir,
)
from wgsextract_cli.core.reference_assets import (
    resolve_reference_library as resolve_reference_library,
)
from wgsextract_cli.core.remote_input import check_url as check_url
from wgsextract_cli.core.remote_input import fetch_index as fetch_index
from wgsextract_cli.core.remote_input import index_files as index_files
from wgsextract_cli.core.remote_input import input_size as input_size
from wgsextract_cli.core.remote_input import is_url as is_url
from wgsextract_cli.core.remote_input import remote_file as remote_file
from wgsextract_cli.core.remote_input import with_index as with_index
from wgsextract_cli.core.resource_estimates import GIB as GIB
from wgsextract_cli.core.resource_estimates import HostRequirement as HostRequirement
from wgsextract_cli.core.resource_estimates import (
    PreflightEstimate as PreflightEstimate,
)
from wgsextract_cli.core.resource_estimates import alignment_hours as alignment_hours
from wgsextract_cli.core.resource_estimates import (
    check_host_requirement as check_host_requirement,
)
from wgsextract_cli.core.resource_estimates import (
    estimate_resources as estimate_resources,
)
from wgsextract_cli.core.resource_estimates import (
    long_read_alignment_hours as long_read_alignment_hours,
)
from wgsextract_cli.core.resource_estimates import long_read_disk as long_read_disk
from wgsextract_cli.core.resource_estimates import (
    preflight_resources as preflight_resources,
)
from wgsextract_cli.core.resource_policy import stage_thread_plan as stage_thread_plan
from wgsextract_cli.core.results import current_result as current_result
from wgsextract_cli.core.steps import Decision as Decision
from wgsextract_cli.core.steps import Step as Step
from wgsextract_cli.core.steps import StepJournal as StepJournal
from wgsextract_cli.core.steps import resume_policy as resume_policy
from wgsextract_cli.core.temp_directory import (
    configured_temp_directory as configured_temp_directory,
)
from wgsextract_cli.core.utils import UsageError as UsageError
from wgsextract_cli.core.utils import WGSExtractError as WGSExtractError
from wgsextract_cli.core.utils import get_resource_defaults as get_resource_defaults
from wgsextract_cli.core.utils import run_pipeline as run_pipeline
from wgsextract_cli.core.variant_files import calculate_bam_md5 as calculate_bam_md5
from wgsextract_cli.core.variant_files import resolve_reference as resolve_reference
from wgsextract_cli.core.variant_files import verify_paths_exist as verify_paths_exist
from wgsextract_cli.core.warnings import get_free_space_needed as get_free_space_needed
from wgsextract_cli.core.warnings import indexed_read_count as indexed_read_count

from . import align as align_cmd
from ._extract_helpers import alignments_to_fastq as alignments_to_fastq
from ._extract_helpers import collate_working_memory as collate_working_memory
from ._extract_helpers import paired_fastq_stream as paired_fastq_stream
from .align import InputReads as InputReads
from .realignment.long_reads import (
    _long_read_chunks as _long_read_chunks,
)
from .realignment.long_reads import (
    _realign_long_reads as _realign_long_reads,
)
from .realignment.long_reads import (
    _tree_bytes as _tree_bytes,
)
from .realignment.mapping import (
    _align as _align,
)
from .realignment.mapping import (
    _align_step as _align_step,
)
from .realignment.mapping import (
    _align_tools as _align_tools,
)
from .realignment.mapping import (
    _index_path as _index_path,
)
from .realignment.mapping import (
    _index_step as _index_step,
)
from .realignment.mapping import (
    _is_cram as _is_cram,
)
from .realignment.mapping import (
    _native_aligner_metrics as _native_aligner_metrics,
)
from .realignment.mapping import (
    _native_gpu_plan_key as _native_gpu_plan_key,
)
from .realignment.mapping import (
    _resume_alignment as _resume_alignment,
)
from .realignment.preparation import (
    _FASTQ_PATTERNS as _FASTQ_PATTERNS,
)
from .realignment.preparation import (
    RealignJob as RealignJob,
)
from .realignment.preparation import (
    _check_gpu as _check_gpu,
)
from .realignment.preparation import (
    _check_long_read_options as _check_long_read_options,
)
from .realignment.preparation import (
    _find_existing_fastqs as _find_existing_fastqs,
)
from .realignment.preparation import (
    _input_cram_reference as _input_cram_reference,
)
from .realignment.preparation import (
    _long_read_reference as _long_read_reference,
)
from .realignment.preparation import (
    _refuse_unpaired_reads as _refuse_unpaired_reads,
)
from .realignment.preparation import (
    _single_end_pacbio as _single_end_pacbio,
)
from .realignment.resources import (
    _disk_fits as _disk_fits,
)
from .realignment.resources import (
    _disk_requirements as _disk_requirements,
)
from .realignment.resources import (
    _long_read_disk_requirements as _long_read_disk_requirements,
)
from .realignment.resources import (
    _minimap2_needs as _minimap2_needs,
)
from .realignment.resources import (
    _Needs as _Needs,
)
from .realignment.resources import (
    _report_resource_estimates as _report_resource_estimates,
)
from .realignment.resources import (
    _require as _require,
)
from .realignment.resources import (
    _short_read_plan as _short_read_plan,
)
from .realignment.steps import (
    _recover_retained_fastqs as _recover_retained_fastqs,
)
from .realignment.steps import (
    _run_steps as _run_steps,
)


def run_realign(params: RealignParams) -> None:
    """The ``realign`` operation (``api.align.realign``)."""
    align_cmd.add_read_inputs(params.r1, params.r2)
    run(params)


def run(params: RealignParams) -> None:
    job = RealignJob.of(params)
    validate_output_profile(job.output_profile or "default")
    if is_url(job.input):
        check_url(job.input)
        remote_file(job.input)  # Reachable, or an error naming it.
    else:
        verify_paths_exist({"--input": job.input})
    # The FASTA that decodes the CRAM, found to detect the platform.
    input_ref = job.input_ref
    input_cram_opt = None
    if not job.platform and not job.r1 and job.input.lower().endswith(".cram"):
        header = get_bam_header(job.input, job.input_ref)
        if not any(group.get("PL") for group in ultima.read_groups(header)):
            input_cram_opt = _input_cram_reference(job)
            input_ref = input_cram_opt[1]
    detected = None
    if not job.platform and not job.r1:
        if input_platform(job.input, input_ref, strict=True) == "pacbio":
            detected = _single_end_pacbio(
                job.input, input_cram_opt or _input_cram_reference(job)
            )
    job.platform = detected or align_cmd.resolve_platform(
        job.platform, job.r1 or job.input, input_ref, job.r2
    )
    if detected:
        record_platform_choice(job.input, detected)
    if is_url(job.input) and not (align_cmd.is_long_read(job) and not job.r1):
        raise UsageError(
            "realign streams an http(s) --input for ONT and PacBio reads only. "
            "Download it first to realign other reads."
        )
    if align_cmd.is_long_read(job) and not job.r1:
        _check_long_read_options(job)
    # Reject bad aligner/platform/preset combinations before hours of extraction.
    selected_aligner = align_cmd.select_aligner(job)
    if selected_aligner == "pbmm2" and configured_temp_directory() and not job.gpu:
        raise UsageError(
            "Native pbmm2's internal sorter does not support --tmp-dir. Use --aligner minimap2 or omit the temporary-directory setting."
        )
    signature = None
    if selected_aligner in align_cmd.BWA_ALIGNERS and not (
        job.ref and Path(job.ref).is_file()
    ):
        signature = calculate_bam_md5(job.input, None)
    target = resolve_reference(job.ref, signature) if job.ref or signature else None
    if job.aligner == "bwa-mem2" and target and Path(target).is_file():
        align_cmd.ensure_fasta_index(target)
        # wgse-bwa-mem3 standing in, and wgse-bwa-mem2 0.5.0 and later, map
        # without bwa-mem2's .0123 file.
        if align_cmd.bwa_mem3_build(
            job, "bwa-mem2", target
        ) is None and not align_cmd.bwa_mem2_index_ready(
            align_cmd.bwa_mem2_memory_model(job.native_aligners), target
        ):
            bwa_mem2.check_index(target)
    if selected_aligner == "bwa-mem3" and not job.gpu:
        # Before hours of extraction: the aligner and its index.
        align_cmd.bwa_mem3_command(job)
        if target and Path(target).is_file():
            align_cmd.require_bwa_mem3_index(target)

    threads, memory = get_resource_defaults(job.threads, job.memory)
    # A URL's output goes to the current directory unless --outdir says.
    outdir = job.outdir or str(
        Path.cwd() if is_url(job.input) else absolute_path(job.input).parent
    )
    Path(outdir).mkdir(parents=True, exist_ok=True)
    # The alignment writes here too, not beside a URL's path.
    job.outdir = outdir

    policy = resume_policy(params)
    if job.r1:
        logical_r1, logical_r2 = job.r1, job.r2
    elif job.platform == "ultima" or align_cmd.is_long_read(job):
        logical_r1, logical_r2 = job.input, None
    else:
        base = input_stem(job.input)
        logical_r1 = f"{base}_R1.fastq.gz"
        logical_r2 = f"{base}_R2.fastq.gz"
    journal_output = align_cmd.aligned_output_path(
        outdir, logical_r1, logical_r2, job.format, selected_aligner
    )
    with StepJournal.for_output(journal_output, "realign", policy) as journal:
        _run_steps(
            job,
            journal,
            selected_aligner=selected_aligner,
            target=target,
            outdir=outdir,
            threads=threads,
            memory=memory,
            input_cram_opt=input_cram_opt,
        )
