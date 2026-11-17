"""Alignment preparation helpers."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from wgsextract_cli.core import bwa_mem2, dense_suffix_array
from wgsextract_cli.core.alignment.fastq import (
    fastq_input,
    stage_read_input,
    validate_fastq,
)
from wgsextract_cli.core.alignment.memory import (
    check_mapping_memory,
)
from wgsextract_cli.core.alignment.profiles import (
    validate_output_profile,
)
from wgsextract_cli.core.alignment.read_counts import fastq_input_statistics
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.genome_library import pair_stem
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.native_aligners import (
    AlignerCommand,
)
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.ref_library.assets import is_fasta_path, reference_genomes_dir
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary
from wgsextract_cli.core.remote_input import is_url
from wgsextract_cli.core.resource_estimates import (
    estimate_resources,
    preflight_resources,
)
from wgsextract_cli.core.resource_policy import stage_thread_plan
from wgsextract_cli.core.samtools_commands import (
    regular_files_bytes,
    sort_memory,
)
from wgsextract_cli.core.staged_alignment import workspace_path
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    get_resource_defaults,
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

from .reads import (
    _is_alignment_file,
    reads_stem,
)
from .selection import (
    requested_aligner,
    short_read_tool,
)

if TYPE_CHECKING:
    pass

from .common import (
    AlignJob,
    InputReads,
    StreamingMemoryUnavailable,
    _AlignSetup,
    aligned_output_path,
    ensure_bwa_alt_file,
    ensure_bwa_index,
)
from .short_reads import (
    aligner_gpu_bytes,
    bwa_mem2_index_ready,
    bwa_mem3_command,
    require_bwa_mem3_index,
    short_read_command,
    stand_in_label,
    streaming_mapping_fits,
)


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
