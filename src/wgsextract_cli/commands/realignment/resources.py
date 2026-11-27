"""Memory, disk and time planning for realignment."""

import logging
import math
from pathlib import Path
from typing import Any, NamedTuple

from wgsextract_cli.core.alignment.memory import (
    estimate_mapping_memory,
    least_mapping_memory,
    reference_bases,
)
from wgsextract_cli.core.alignment.samtools_commands import (
    parse_memory,
    regular_files_bytes,
    sort_memory,
)
from wgsextract_cli.core.chunked_alignment import (
    ChunkPlan,
    chunked_disk,
    contigs,
)
from wgsextract_cli.core.native_aligners import AlignerCommand
from wgsextract_cli.core.remote_input import (
    input_size,
)
from wgsextract_cli.core.resource_estimates import (
    GIB,
    HostRequirement,
    PreflightEstimate,
    alignment_hours,
    check_host_requirement,
    estimate_resources,
    long_read_alignment_hours,
    long_read_disk,
    preflight_resources,
)
from wgsextract_cli.core.resource_policy import stage_thread_plan
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.warnings import get_free_space_needed

from .. import align as align_cmd
from .preparation import (
    RealignJob,
)


def _disk_requirements(
    input_path: str,
    outdir: str,
    need_extract: bool,
    reads: int | None,
    keep_fastq: bool = False,
    *,
    streaming: bool = False,
) -> list[tuple[str, int]]:
    """The peak disk use (bytes) in each directory a short-read realign writes."""
    file_size = Path(input_path).stat().st_size
    is_cram = input_path.lower().endswith(".cram")

    extract_temp_gb, extract_final_gb = (0, 0)
    if need_extract or streaming:
        extract_temp_gb, extract_final_gb = get_free_space_needed(
            file_size, sort_type="Name", is_cram=is_cram, reads=reads
        )
        if streaming:
            extract_final_gb = 0

    align_temp_gb, align_final_gb = get_free_space_needed(
        file_size, sort_type="Coord", is_cram=is_cram, reads=reads
    )
    logging.debug(
        "Realign disk: extract %d+%d GB, align %d+%d GB (temporary+final).",
        extract_temp_gb,
        extract_final_gb,
        align_temp_gb,
        align_final_gb,
    )
    scratch = configured_temp_directory()
    if scratch and Path(scratch).stat().st_dev != Path(outdir).stat().st_dev:
        retained_fastq = extract_final_gb if keep_fastq else 0
        staged_fastq = 0 if keep_fastq else extract_final_gb
        return [
            (outdir, (retained_fastq + align_final_gb) * GIB),
            (scratch, (staged_fastq + max(extract_temp_gb, align_temp_gb)) * GIB),
        ]
    # Collate writes every bucket before it emits a read, then deletes each one as
    # it drains while the sorter's spills grow. Streaming therefore peaks like
    # file extraction without the FASTQs between the two stages.
    total_gb = max(extract_temp_gb, extract_final_gb + align_temp_gb + align_final_gb)
    return [(outdir, total_gb * GIB)]


def _long_read_disk_requirements(
    input_path: str,
    outdir: str,
    *,
    output_format: str,
    extract: bool,
    threads: str,
    memory: str,
    plan: ChunkPlan | None = None,
    reference: str | None = None,
    done_bytes: int = 0,
) -> list[tuple[str, int]]:
    """Long reads stream into the aligner without a collate: the peak is the
    sorter's spills beside its output, and an extracted FASTQ when kept. A
    chunked alignment (``plan``) spills one chunk at a time, but keeps the
    aligner's index of ``reference`` and, while it assembles the output, one
    group beside it; ``done_bytes`` of that are already in its work
    directory."""
    amount, unit = parse_memory(memory)
    sort_buffer = int(threads) * amount * (GIB if unit == "G" else 1024**2)
    size = input_size(input_path)
    disk = long_read_disk(input_path, size, output_format, sort_buffer)
    fastq = disk.fastq if extract else 0
    spill, output = disk.spill, disk.output
    if plan:
        largest = max(plan.sizes)
        chunk = long_read_disk(input_path, largest, output_format, sort_buffer)
        sequences = contigs(reference) if reference else []
        output, spill = chunked_disk(
            disk.output, disk.spill, chunk.spill, plan, sequences
        )
        output = max(0, output - done_bytes)
    scratch = configured_temp_directory()
    if scratch and Path(scratch).stat().st_dev != Path(outdir).stat().st_dev:
        return [(outdir, output + fastq), (scratch, spill)]
    return [(outdir, spill + output + fastq)]


def _disk_fits(disk: list[tuple[str, int]]) -> bool:
    """Whether every directory's filesystem has room for its share of ``disk``."""
    requirement = HostRequirement("realign", tuple(disk), None, 1, None)
    return not any(volume.short for volume in requirement.volumes())


def _require(
    job: "RealignJob",
    disk: list[tuple[str, int]],
    threads: str,
    needs: "_Needs",
) -> None:
    """Print realign's host requirement and refuse what this computer lacks,
    unless --no-preflight (#690)."""
    check_host_requirement(
        HostRequirement(
            "realign",
            (*(needs.staged_disk or tuple(disk)), *needs.disk),
            needs.memory,
            int(threads),
            needs.hours,
            memory_floor=needs.floor,
        ),
        enforce=not job.no_preflight,
        disk_advice="Free up space or choose an --outdir on a bigger disk.",
        memory_advice="Lower --threads or --memory.",
    )


def _minimap2_needs(
    job: "RealignJob",
    paths: list[str],
    reference: str | None,
    threads: str,
    memory: str,
    *,
    reads: int | None = None,
    samblaster: bool = False,
    streaming: bool = False,
    additional_ram_bytes: int = 0,
) -> "_Needs":
    """minimap2's RAM at these settings and what it can't run without (its
    index), as align sizes them for the read files ``paths`` (local files
    bound its sort and batches; a URL doesn't), and for long reads the
    approximate hours to realign them. The floor needs the reference's base
    count: a gzipped FASTA's size only bounds it loosely."""
    files = regular_files_bytes(paths)
    resolved = reference if reference and Path(reference).is_file() else None
    preset = align_cmd._minimap2_preset(job)
    options: dict[str, Any] = {
        "samblaster": samblaster,
        "reads": reads,
        "platform": job.platform,
        "preset": preset,
        "input_bytes": None if streaming else files,
    }
    hours = None
    if preset != "sr":
        size = sum(input_size(path) for path in paths)
        hours = long_read_alignment_hours(
            paths[0], size, stage_thread_plan(job.threads).alignment
        )
    counted = resolved is not None and (
        reference_bases(resolved) > 0
        or not resolved.lower().endswith((".gz", ".bgz", ".bgzf"))
    )
    try:
        sorting = sort_memory(threads, memory, input_bytes=files, log=False)
        need = estimate_mapping_memory(
            resolved,
            "minimap2",
            threads=threads,
            alignment_threads=str(stage_thread_plan(job.threads).alignment),
            memory=sorting,
            **options,
        ).total
        least = (
            least_mapping_memory(resolved, "minimap2", **options) if counted else None
        )
    except (OSError, ValueError) as error:
        logging.debug("minimap2's memory wasn't estimated: %s", error)
        return _Needs(hours=hours)
    return _Needs(
        need + additional_ram_bytes,
        least + additional_ram_bytes if least is not None else None,
        hours,
    )


def _short_read_plan(
    job: RealignJob,
    ref: str,
    *,
    threads: str,
    memory: str,
    samblaster: bool,
    reads: int | None,
    read_length: float | None,
    additional_ram_bytes: int = 0,
) -> tuple[AlignerCommand, str]:
    """How the alignment step will run BWA-MEM on ``ref`` (as _bwa_setup
    chooses), and its sort memory per thread, for checks before extraction."""
    if align_cmd.short_read_tool(job) == "bwa-mem3":
        return align_cmd.bwa_mem3_command(job), memory
    return align_cmd.short_read_command(
        job,
        ref,
        threads=threads,
        memory=memory,
        samblaster=samblaster,
        reads=reads,
        read_length=read_length,
        additional_ram_bytes=additional_ram_bytes,
        announce=False,
    )


class _Needs(NamedTuple):
    """An alignment's RAM at its settings and what it can't run without (its
    floor), each None when not estimated, its approximate hours, and disk it
    needs beyond the alignment's (reference indexes to build)."""

    memory: int | None = None
    floor: int | None = None
    hours: float | None = None
    disk: tuple[tuple[str, int], ...] = ()
    staged_disk: tuple[tuple[str, int], ...] = ()


def _report_resource_estimates(
    job: RealignJob,
    reference: str | None,
    outdir: str,
    threads: str,
    memory: str,
    reads: int | None,
    *,
    input_paths: list[str],
    need_extract: bool,
    paired: bool,
    read_length: float | None = None,
    additional_ram_bytes: int = 0,
) -> _Needs:
    """Report the alignment's resource estimates; return its RAM and time."""
    chosen: list[AlignerCommand] = []
    floors: list[int] = []

    def estimate() -> PreflightEstimate:
        effective_threads = threads
        alignment_threads = str(stage_thread_plan(job.threads).alignment)
        samblaster = align_cmd.duplicate_marker(paired=paired) == "samblaster"
        resolved = reference if reference and Path(reference).is_file() else None
        tool = align_cmd.short_read_tool(job)
        # As align lowers it for what the reads can fill (#781).
        filled = sort_memory(
            threads,
            memory,
            input_bytes=regular_files_bytes([p for p in input_paths if p]),
            log=False,
        )
        sorting = align_cmd.short_read_sort_memory(tool, filled, log=False)
        gpu_bytes = dense_sa_bytes = 0
        staged = False
        if resolved:
            # The aligner that will run, bwa-mem3 standing in included.
            aligner, sorting = _short_read_plan(
                job,
                resolved,
                threads=threads,
                memory=filled,
                samblaster=samblaster,
                reads=reads,
                read_length=read_length,
                additional_ram_bytes=additional_ram_bytes,
            )
            chosen.append(aligner)
            alignment_threads = str(
                stage_thread_plan(job.threads, metal=aligner.gpu).alignment
            )
            tool = aligner.memory_model
            gpu_bytes = align_cmd.aligner_gpu_bytes(aligner, resolved, reads)
            dense_sa_bytes = aligner.dense_sa_bytes
            staged = aligner.staged
            if reference_bases(resolved) > 0:
                # Without the reference's size (its .fai or BWA's .ann) a
                # human reference stands in, even for a tiny one.
                floors.append(
                    least_mapping_memory(
                        resolved,
                        tool,
                        memory=sorting,
                        samblaster=samblaster,
                        reads=reads,
                        platform=job.platform,
                        read_length=read_length,
                        gpu_bytes=gpu_bytes,
                        dense_sa_bytes=dense_sa_bytes,
                        staged=staged,
                    )
                )
        return estimate_resources(
            "realign",
            input_paths=[path for path in input_paths if path],
            output_dir=outdir,
            temp_dir=configured_temp_directory(),
            reference=resolved,
            threads=effective_threads,
            alignment_threads=alignment_threads,
            memory=sorting,
            aligner=tool,
            samblaster=samblaster,
            reads=reads,
            platform=job.platform,
            read_length=read_length,
            additional_ram_bytes=additional_ram_bytes,
            gpu_bytes=gpu_bytes,
            dense_sa_bytes=dense_sa_bytes,
            staged=staged,
            keep_fastq=job.keep_fastq,
            staged_workspace=(
                str(
                    align_cmd.workspace_path(
                        align_cmd.aligned_output_path(
                            outdir,
                            input_paths[0],
                            None
                            if need_extract
                            else (input_paths[1] if len(input_paths) > 1 else None),
                            job.format,
                        )
                    )
                )
                if staged
                else None
            ),
            output_format=job.format,
            output_profile=job.output_profile or "default",
            extract_fastq=need_extract,
            fastq_dir=outdir if job.keep_fastq else configured_temp_directory(),
            duplicate_marking=(
                "gatk-flow"
                if job.platform == "ultima"
                and align_cmd.get_tool_path("gatk") is not None
                else None
            ),
        )

    reported = preflight_resources(estimate)
    # BWA builds a missing index beside the reference before it aligns.
    indexes = tuple(
        (row.paths[0], row.components["reference_indexes"])
        for row in (reported.resources if reported else ())
        if row.paths and "reference_indexes" in row.components
    )
    ram = (
        next(
            (r.estimated_bytes for r in reported.resources if r.resource == "memory"),
            None,
        )
        if reported
        else None
    )
    engine = chosen[0].engine if chosen else align_cmd.short_read_tool(job)
    count = reads
    if count is None:
        # As estimate_resources infers it from the input's size.
        size = sum(input_size(path) for path in input_paths if path)
        cram = bool(input_paths) and input_paths[0].lower().endswith(".cram")
        count = math.ceil(size / (12 if cram else 80))
    length = read_length or (300 if job.platform == "ultima" else 150)
    hours = alignment_hours(
        engine,
        bool(chosen and chosen[0].gpu),
        count * length,
        stage_thread_plan(job.threads, metal=bool(chosen and chosen[0].gpu)).alignment,
    )
    staged_disk = (
        tuple(
            (row.paths[0], row.estimated_bytes)
            for row in (reported.resources if reported else ())
            if row.resource == "disk" and row.paths and row.estimated_bytes is not None
        )
        if chosen and chosen[0].staged
        else ()
    )
    return _Needs(
        ram,
        floors[0] if floors else None,
        hours,
        () if staged_disk else indexes,
        staged_disk,
    )
