"""Alignment backends helpers."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from wgsextract_cli.core import ultima
from wgsextract_cli.core.alignment.fastq import (
    check_primary_reads,
)
from wgsextract_cli.core.alignment.memory import (
    available_memory,
    check_mapping_memory,
    estimate_mapping_memory,
)
from wgsextract_cli.core.alignment.profiles import (
    output_profile_options,
)
from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.atomic_output import (
    atomic_outputs_enabled,
    safe_output,
)
from wgsextract_cli.core.chunked_alignment import ChunkedAlignment
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.native_aligners import (
    AlignerCommand,
    find_native_aligner,
)
from wgsextract_cli.core.native_support import require_native_tool
from wgsextract_cli.core.paths import java_local_path
from wgsextract_cli.core.read_counts import fastq_input_statistics
from wgsextract_cli.core.ref_library.assets import is_fasta_path
from wgsextract_cli.core.resource_estimates import (
    estimate_resources,
    preflight_resources,
)
from wgsextract_cli.core.resource_policy import stage_thread_plan
from wgsextract_cli.core.samtools_commands import (
    regular_files_bytes,
)
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    get_sam_sort_cmd,
    run_command,
    run_pipeline,
)

from .._extract_helpers import cleanup_files
from .output import (
    _index_output,
    record_alignment,
    run_streamed_alignment,
)
from .read_groups import (
    _ultima_read_group,
    read_group,
    read_group_descriptions,
    ultima_read_groups,
)
from .reads import (
    LONG_READ_TAGS,
    _is_alignment_file,
    _long_read_length,
    long_read_fastq_command,
)
from .selection import (
    _PBMM2_PRESETS,
    _minimap2_preset,
    is_long_read,
)

if TYPE_CHECKING:
    pass

from .common import (
    BWA_BATCH_BASES,
    AlignJob,
    _AlignSetup,
    duplicate_marker,
)
from .preparation import (
    _bwa_setup,
    _prepare_alignment,
)
from .short_reads import (
    aligner_gpu_bytes,
)

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
