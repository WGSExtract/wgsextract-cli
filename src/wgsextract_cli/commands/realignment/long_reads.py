"""Chunked and single-pass long-read realignment."""

import logging
import shutil
from dataclasses import replace
from pathlib import Path

from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.chunked_alignment import (
    ChunkPlan,
    parse_size,
    plan_chunks,
)
from wgsextract_cli.core.dependency_checks import (
    verify_dependencies,
)
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.remote_input import (
    fetch_index,
    index_files,
    input_size,
    is_url,
    with_index,
)
from wgsextract_cli.core.resource_policy import stage_thread_plan
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.steps import Step, StepJournal
from wgsextract_cli.core.utils import (
    run_pipeline,
)

from .. import align as align_cmd
from .mapping import (
    _align,
    _align_step,
    _index_step,
    _resume_alignment,
)
from .preparation import (
    RealignJob,
    _input_cram_reference,
    _long_read_reference,
)
from .resources import (
    _long_read_disk_requirements,
    _minimap2_needs,
    _require,
)


def _tree_bytes(path: Path) -> int:
    """The bytes of the files under ``path`` (0 when it doesn't exist)."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _long_read_chunks(
    job: RealignJob, selected_aligner: str, chunk_bytes: int, outdir: str
) -> tuple[ChunkPlan, str] | None:
    """The chunks a long-read realignment aligns one at a time, and the
    input the chunks read (a URL with its index, fetched into ``outdir``);
    None to align the input in one pass: chunking is off, the input is
    smaller than a chunk, or it isn't a coordinate-sorted, indexed BAM/CRAM.
    The sr preset marks duplicates, which needs the whole alignment: one
    pass too."""
    if selected_aligner != "minimap2" or job.keep_fastq or not chunk_bytes:
        return None
    if align_cmd._minimap2_preset(job) == "sr":
        return None
    size = input_size(job.input)
    if size <= chunk_bytes:
        return None
    source, index = job.input, None
    if is_url(job.input):
        index = fetch_index(job.input, outdir)
        if index:
            source = with_index(job.input, index)
    plan = None
    if index or not is_url(job.input):
        plan = plan_chunks(
            job.input,
            get_bam_header(job.input),
            chunk_bytes,
            index=index,
            input_bytes=size,
        )
    if plan is None:
        logging.info(
            "Aligning %s in one pass: chunks need a coordinate-sorted, indexed "
            "BAM or CRAM.",
            job.input,
        )
        return None
    if len(plan.chunks) < 2:
        return None
    logging.info(
        "Aligning %s in %d chunks of about %s each.",
        job.input,
        len(plan.chunks),
        job.chunk_size,
    )
    return plan, source


def _realign_long_reads(
    job: RealignJob,
    journal: StepJournal,
    *,
    selected_aligner: str,
    target: str | None,
    outdir: str,
    threads: str,
    memory: str,
    input_cram_opt: list[str] | None,
) -> None:
    """ONT and PacBio reads are single-end and carry base-modification tags:
    each read's primary record, tags included, streams into minimap2. With
    --keep-fastq they are extracted to a tagged <input stem>.fastq.gz first."""
    out_path = align_cmd.aligned_output_path(
        outdir, job.input, None, job.format, selected_aligner
    )
    # The aligner names its output after the FASTQ, so it takes the stem that
    # names out_path (movie.ccs.bam -> movie.fastq.gz -> movie_aligned.bam).
    fastq = str(
        absolute_path(Path(outdir, f"{align_cmd.reads_stem(job.input)}.fastq.gz"))
    )
    extract_step = Step(
        "extract",
        inputs={
            "alignment": job.input,
            "input_reference": job.input_ref,
        },
        outputs=[fastq],
        params={"tags": align_cmd.LONG_READ_TAGS},
        tools=["samtools", "bgzip"],
    )
    align_step = _align_step(
        job, selected_aligner, target, out_path, {"alignment": job.input}
    )
    if job.keep_fastq and job.force_extract:
        journal.policy = replace(
            journal.policy, force_steps=journal.policy.force_steps | {"extract"}
        )
    if not journal.policy.forced("extract") and not journal.check(align_step).run:
        # An earlier run finished: nothing before the alignment is needed.
        _resume_alignment(
            journal, journal.decide(align_step), out_path, target, threads
        )
        if not journal.dry_run:
            shutil.rmtree(out_path + ".chunks", ignore_errors=True)
        if job.keep_fastq and not journal.dry_run:
            if Path(fastq).is_file():
                current_result().add_output(fastq, "fastq_r1")
            else:
                logging.info(
                    "The alignment is complete, so no FASTQ was extracted; "
                    "--force-extract writes one and realigns."
                )
        return
    extract_decision = journal.decide(extract_step) if job.keep_fastq else None
    decision = journal.decide(
        align_step, after=[extract_decision] if extract_decision else []
    )
    if journal.dry_run:
        journal.decide(_index_step(out_path))
        return
    cram_opt = input_cram_opt or _input_cram_reference(job)
    chunk_bytes = parse_size(job.chunk_size)
    chunking = _long_read_chunks(job, selected_aligner, chunk_bytes, outdir)
    plan, chunk_input = chunking or (None, None)
    workdir = Path(out_path + ".chunks")
    if not plan and workdir.exists():
        logging.info("Removing %s, left by an interrupted chunked run.", workdir)
        shutil.rmtree(workdir)
    # The FASTA alignment resolves (from the input's signature without
    # --ref): minimap2 indexes it, as do the chunks.
    reference = _long_read_reference(job, target)
    disk = _long_read_disk_requirements(
        job.input,
        outdir,
        output_format=job.format,
        extract=bool(extract_decision and extract_decision.run),
        threads=threads,
        memory=memory,
        plan=plan,
        reference=reference if plan else None,
        # An interrupted run's chunks are kept or, if they can't be
        # resumed, deleted: either way, that space counts as free.
        done_bytes=_tree_bytes(workdir),
    )
    _require(
        job,
        disk,
        threads,
        _minimap2_needs(job, [job.input], reference, threads, memory),
    )
    # samtools fastq's -T lists tags; the reference needs --reference.
    decode = ["--reference", cram_opt[1]] if cram_opt else []
    align_job = replace(
        job,
        r1=job.input,
        input_cram_opt=decode,
        chunks=plan.chunks if plan else None,
        resume_chunks=decision.partial,
        chunk_input=chunk_input,
    )
    if extract_decision is not None:
        if extract_decision.run:
            verify_dependencies(["samtools", "bgzip"])
            logging.info("Extracting tagged single-end FASTQ from %s ...", job.input)
            with (
                journal.running(extract_decision),
                atomic_output(fastq) as staged,
                open(staged, "wb") as handle,
            ):
                run_pipeline(
                    [
                        align_cmd.long_read_fastq_command(job.input, decode),
                        [
                            "bgzip",
                            "-@",
                            str(stage_thread_plan(job.threads).compression),
                            "-c",
                        ],
                    ],
                    stdout=handle,
                    description="FASTQ extraction",
                    heartbeat=True,
                )
        align_job.r1 = fastq
        align_job.read_group_description = align_cmd.read_group_descriptions(
            job.input, job.input_ref
        )
    _align(
        journal,
        decision,
        lambda: align_cmd.run(align_job),
        keep_partial=bool(plan) and decision.partial,
    )
    # Only now that the align step is recorded: a run killed before keeps
    # the chunks' finished state, so its rerun doesn't realign them.
    shutil.rmtree(workdir, ignore_errors=True)
    if is_url(job.input):
        # Downloaded by this run or an interrupted one, chunked or not.
        for index in index_files(job.input, outdir):
            index.unlink(missing_ok=True)
    if job.keep_fastq:
        current_result().add_output(fastq, "fastq_r1")
