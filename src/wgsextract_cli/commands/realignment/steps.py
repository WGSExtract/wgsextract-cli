"""Resumable short-read extraction and alignment workflow."""

import logging
import os
import stat
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

from wgsextract_cli.core.alignment.memory import (
    available_memory,
    check_mapping_memory,
)
from wgsextract_cli.core.alignment.read_counts import (
    alignment_sample,
    fastq_input_statistics,
)
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.ref_library.assets import (
    input_stem,
)
from wgsextract_cli.core.resource_policy import stage_thread_plan
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.steps import Decision, Step, StepJournal
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.variant_files import (
    verify_paths_exist,
)
from wgsextract_cli.core.warnings import indexed_read_count

from .. import align as align_cmd
from .._extract_helpers import (
    alignments_to_fastq,
    collate_working_memory,
    paired_fastq_stream,
)
from ..align import InputReads
from .long_reads import (
    _realign_long_reads,
)
from .mapping import (
    _align,
    _align_step,
    _align_streamed,
    _index_step,
    _resume_alignment,
)
from .preparation import (
    RealignJob,
    _check_gpu,
    _find_existing_fastqs,
    _input_cram_reference,
    _refuse_unpaired_reads,
)
from .resources import (
    _disk_fits,
    _disk_requirements,
    _long_read_disk_requirements,
    _minimap2_needs,
    _Needs,
    _report_resource_estimates,
    _require,
    _short_read_plan,
)


def _single_part_minimap2_reference(target: str | None) -> bool:
    # minimap2 rereads its queries for each index part. An uncompressed FASTA's
    # byte size bounds its base count below the short-read index limit (-I 8G).
    if not target:
        return False
    path = Path(target)
    if not path.is_file() or path.stat().st_size >= 8_000_000_000:
        return False
    with path.open("rb") as fasta:
        return fasta.read(1) == b">"


def _run_steps(
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
    """Realign as steps: extract FASTQs (when they're written to files), then
    align. A completed alignment skips everything before it."""
    if align_cmd.is_long_read(job) and not job.r1:
        _realign_long_reads(
            job,
            journal,
            selected_aligner=selected_aligner,
            target=target,
            outdir=outdir,
            threads=threads,
            memory=memory,
            input_cram_opt=input_cram_opt,
        )
        return

    if job.platform == "ultima" and not job.r1:
        out_path = align_cmd.aligned_output_path(
            outdir, job.input, None, job.format, selected_aligner
        )
        step = _align_step(
            job, selected_aligner, target, out_path, {"alignment": job.input}
        )
        decision = journal.decide(step)
        if not decision.run:
            _resume_alignment(journal, decision, out_path, target, threads)
            return
        if journal.dry_run:
            journal.decide(_index_step(out_path))
            return
        # Single-end reads with flow tags: streamed into the aligner, no FASTQs.
        reads = indexed_read_count(job.input, threads)
        needs = _report_resource_estimates(
            job,
            target,
            outdir,
            threads,
            memory,
            reads,
            input_paths=[job.input],
            need_extract=False,
            paired=False,
        )
        disk = _disk_requirements(job.input, outdir, need_extract=False, reads=reads)
        _require(
            job,
            disk,
            threads,
            needs,
        )
        align_job = replace(job, r1=job.input, input_reads=InputReads(reads))
        cram_reference = _input_cram_reference(job)
        _align(
            journal,
            decision,
            lambda: align_cmd.align_ultima(align_job, cram_reference),
        )
        return

    # 1. Resolve FASTQs: the user's, an earlier run's extraction, a pair found
    # beside the input, or a new extraction (or stream).
    base = input_stem(job.input)
    staging = outdir if job.keep_fastq else configured_temp_directory() or outdir
    staged = (
        str(absolute_path(Path(staging, f"{base}_R1.fastq.gz"))),
        str(absolute_path(Path(staging, f"{base}_R2.fastq.gz"))),
    )
    extract_step = Step(
        "extract",
        inputs={
            "alignment": job.input,
            "input_reference": job.input_ref,
        },
        outputs=staged,
        tools=["samtools"],
    )
    reads_inputs: dict[str, str | None] = (
        {"fastq_r1": job.r1, "fastq_r2": job.r2} if job.r1 else {"alignment": job.input}
    )
    out_path = align_cmd.aligned_output_path(
        outdir,
        job.r1 or staged[0],
        job.r2 if job.r1 else staged[1],
        job.format,
        selected_aligner,
    )
    align_step = _align_step(job, selected_aligner, target, out_path, reads_inputs)
    if (
        not job.r1
        and not job.force_extract
        and not journal.policy.forced("extract")
        and not journal.check(align_step).run
    ):
        # An earlier run finished: nothing before the alignment is needed.
        _resume_alignment(
            journal, journal.decide(align_step), out_path, target, threads
        )
        return

    if not job.r1 and not job.force_extract and not job.keep_fastq:
        recovered = _recover_retained_fastqs(journal, extract_step, staged, staging)
        if recovered is not None:
            extract_step, staged = recovered

    extract_decision: Decision | None = None
    if job.r1:
        verify_paths_exist({"--r1": job.r1})
        if job.r2:
            verify_paths_exist({"--r2": job.r2})
        # A single-end --r1 leaves r2_path None, as before.
        r1_path, r2_path = job.r1, cast(str, job.r2)
        need_extract = False
    elif not job.force_extract and not journal.check(extract_step).run:
        extract_decision = journal.decide(extract_step)
        r1_path, r2_path = staged
        need_extract = False
    elif (
        not job.force_extract
        and not journal.policy.forced("extract")
        and (existing := _find_existing_fastqs(job.input, outdir))
        # This run's own extraction, when its record doesn't vouch for it.
        and not (existing == staged and journal.recorded("extract"))
    ):
        r1_path, r2_path = existing
        need_extract = False
        logging.info("Reusing existing FASTQs:\n  R1: %s\n  R2: %s", r1_path, r2_path)
        current_result().add_input(r1_path, "fastq_r1")
        current_result().add_input(r2_path, "fastq_r2")
        out_path = align_cmd.aligned_output_path(
            outdir, r1_path, r2_path, job.format, selected_aligner
        )
        align_step = _align_step(
            job,
            selected_aligner,
            target,
            out_path,
            {"alignment": job.input, "fastq_r1": r1_path, "fastq_r2": r2_path},
        )
    else:
        r1_path, r2_path = staged
        need_extract = True

    refresh_cached_fastqs = (
        need_extract
        and (job.force_extract or journal.policy.forced("extract"))
        and any(Path(path).is_file() for path in (r1_path, r2_path) if path)
    )
    bwa = selected_aligner in align_cmd.BWA_ALIGNERS
    minimap2 = selected_aligner == "minimap2"
    streaming = (
        need_extract
        and not job.keep_fastq
        and not job.gpu
        and not refresh_cached_fastqs
        and (
            bwa
            or (
                minimap2
                and align_cmd._minimap2_preset(job) == "sr"
                and job.ref is not None
                and Path(job.ref).is_file()
                and _single_part_minimap2_reference(target)
            )
        )
    )
    align_decision: Decision | None = None
    if journal.dry_run or not need_extract:
        if need_extract and not streaming:
            extract_decision = journal.decide(extract_step)
        align_decision = journal.decide(
            align_step, after=[extract_decision] if extract_decision else []
        )
        if not align_decision.run:
            _resume_alignment(journal, align_decision, out_path, target, threads)
            return
        if journal.dry_run:
            journal.decide(_index_step(out_path))
            return
    if job.gpu:
        # Only once the alignment will run: a dry run or a finished one needs no GPU.
        _check_gpu(job)
    statistics = None if need_extract else fastq_input_statistics(r1_path, r2_path)
    reads = (
        indexed_read_count(job.input, threads)
        if need_extract
        else statistics.reads
        if statistics is not None
        else None
    )
    read_length = statistics.read_length if statistics is not None else None
    additional_ram_bytes = 0
    cram_opt: list[str] = []
    if need_extract:
        verify_dependencies(["samtools"])
        cram_opt = (
            input_cram_opt if input_cram_opt is not None else _input_cram_reference(job)
        )
        _refuse_unpaired_reads(job.input, cram_opt)
    if streaming:
        sample = alignment_sample(job.input, cram_opt=cram_opt)
        read_length = sample.read_length
        # One collate bucket is resident while the mapper runs; reserve it alongside.
        additional_ram_bytes = collate_working_memory(
            job.input,
            threads,
            memory,
            records=reads,
            max_record_bytes=sample.max_record_bytes,
        )
        fits = additional_ram_bytes <= available_memory()
        if fits and target and Path(target).is_file() and bwa:
            samblaster = (
                align_cmd.duplicate_marker(paired=bool(r2_path)) == "samblaster"
            )
            aligner, aligner_memory = _short_read_plan(
                job,
                target,
                threads=threads,
                memory=memory,
                samblaster=samblaster,
                reads=reads,
                read_length=read_length,
                additional_ram_bytes=additional_ram_bytes,
            )
            fits = align_cmd.streaming_mapping_fits(
                target,
                aligner.memory_model,
                threads=threads,
                alignment_threads=str(
                    stage_thread_plan(job.threads, metal=aligner.gpu).alignment
                ),
                memory=aligner_memory,
                samblaster=samblaster,
                reads=reads,
                platform=job.platform,
                read_length=read_length,
                additional_ram_bytes=additional_ram_bytes,
                gpu_bytes=align_cmd.aligner_gpu_bytes(aligner, target, reads),
                dense_sa_bytes=aligner.dense_sa_bytes,
            )
        elif fits and minimap2:
            needs = _minimap2_needs(
                job,
                [job.input],
                target,
                threads,
                memory,
                reads=reads,
                samblaster=align_cmd.duplicate_marker(paired=True) == "samblaster",
                streaming=True,
                additional_ram_bytes=additional_ram_bytes,
            )
            fits = needs.memory is not None and needs.memory <= available_memory()
        if not fits:
            logging.info(
                "Holding a collate bucket (~%.1f GiB) beside alignment does not "
                "fit available RAM; extracting FASTQs without changing the "
                "aligner.",
                additional_ram_bytes / 1024**3,
            )
            streaming = False
            additional_ram_bytes = 0
    needs = _Needs()
    if bwa and not job.gpu:
        needs = _report_resource_estimates(
            job,
            target,
            outdir,
            threads,
            memory,
            reads,
            input_paths=[job.input] if need_extract else [r1_path, r2_path],
            need_extract=need_extract and not streaming,
            paired=bool(r2_path),
            read_length=read_length,
            additional_ram_bytes=additional_ram_bytes,
        )
    elif minimap2 and not job.gpu:
        # Short reads with --aligner minimap2 (--preset sr).
        needs = _minimap2_needs(
            job,
            [job.input] if need_extract else [p for p in (r1_path, r2_path) if p],
            target,
            threads,
            memory,
            reads=reads,
            samblaster=align_cmd.duplicate_marker(paired=bool(r2_path)) == "samblaster",
            streaming=streaming,
            additional_ram_bytes=additional_ram_bytes,
        )
    if job.aligner == "bwa-mem2" and target and Path(target).is_file():
        # As align's: bwa-mem2, or wgse-bwa-mem3 in its place, must fit.
        samblaster = align_cmd.duplicate_marker(paired=bool(r2_path)) == "samblaster"
        aligner, aligner_memory = _short_read_plan(
            job,
            target,
            threads=threads,
            memory=memory,
            samblaster=samblaster,
            reads=reads,
            read_length=read_length,
        )
        check_mapping_memory(
            target,
            aligner.memory_model,
            threads=threads,
            alignment_threads=str(
                stage_thread_plan(job.threads, metal=aligner.gpu).alignment
            ),
            memory=aligner_memory,
            samblaster=samblaster,
            reads=reads,
            read_length=read_length,
            platform=job.platform,
            additional_ram_bytes=0,
            gpu_bytes=align_cmd.aligner_gpu_bytes(aligner, target, reads),
            dense_sa_bytes=aligner.dense_sa_bytes,
            required=True,
            label=align_cmd.stand_in_label(aligner),
        )

    # 2. The host requirement: disk, RAM and time.
    if align_cmd.is_long_read(job) and job.r1:
        disk = _long_read_disk_requirements(
            job.r1,
            outdir,
            output_format=job.format,
            extract=False,
            threads=threads,
            memory=memory,
        )
        if minimap2:
            needs = _minimap2_needs(job, [job.r1], target, threads, memory)
    else:
        disk = _disk_requirements(
            job.input,
            outdir,
            need_extract=need_extract and not streaming,
            reads=reads,
            keep_fastq=job.keep_fastq,
            streaming=streaming,
        )
        if streaming and not job.no_preflight and not _disk_fits(disk):
            logging.info(
                "Streaming spills do not fit available disk; extracting "
                "FASTQs without changing the aligner."
            )
            streaming = False
            additional_ram_bytes = 0
            needs = _extracted_alignment_needs(
                job, target, outdir, threads, memory, reads, read_length, minimap2
            )
            disk = _disk_requirements(
                job.input,
                outdir,
                need_extract=True,
                reads=reads,
                keep_fastq=job.keep_fastq,
            )
    _require(
        job,
        disk,
        threads,
        needs,
    )

    if streaming:
        logging.info(
            "Streaming paired FASTQ from %s into %s; no gzipped FASTQ files.",
            job.input,
            "minimap2" if minimap2 else "BWA",
        )
        # Logical mate names preserve existing output stems/read groups, not read paths.
        align_job = replace(
            job,
            r1=r1_path,
            r2=r2_path,
            input_reads=InputReads(reads, read_length),
        )
        align_decision = journal.decide(align_step)
        mapper = align_cmd.align_minimap2 if minimap2 else align_cmd.align_bwa
        try:
            with paired_fastq_stream(
                job.input,
                threads=str(stage_thread_plan(job.threads).compression),
                memory=memory,
                memory_threads=threads,
                temp_parent=outdir,
                cram_opt=cram_opt,
            ) as stream:
                _align_streamed(
                    journal,
                    align_decision,
                    lambda before_write: mapper(
                        align_job,
                        upstream=stream.stages,
                        discarded_fastq=stream.discarded_fastq,
                        read_paths=[job.input],
                        additional_ram_bytes=additional_ram_bytes,
                        before_write=before_write,
                    ),
                )
        except align_cmd.StreamingMemoryUnavailable:
            logging.info(
                "Available RAM changed before streaming started; extracting "
                "FASTQs without changing the aligner."
            )
            needs = _extracted_alignment_needs(
                job, target, outdir, threads, memory, reads, read_length, minimap2
            )
            disk = _disk_requirements(
                job.input,
                outdir,
                need_extract=True,
                reads=reads,
                keep_fastq=job.keep_fastq,
            )
            _require(job, disk, threads, needs)
        else:
            return

    # 3. Extract if needed, publishing each FASTQ only once it is complete.
    if need_extract:
        verify_dependencies(["samtools"])
        log_dependency_info(["samtools"])
        extract_decision = journal.decide(extract_step)
        logging.info("Extracting FASTQs from %s ...", job.input)
        with (
            journal.running(extract_decision) as extracting,
            atomic_output(r1_path) as staged_r1,
            atomic_output(r2_path) as staged_r2,
        ):
            extracting.metrics["temporary"] = not job.keep_fastq
            alignments_to_fastq(
                job.input,
                staged_r1,
                staged_r2,
                threads=str(stage_thread_plan(job.threads).compression),
                memory=memory,
                memory_threads=threads,
                temp_parent=outdir,
                cram_opt=cram_opt,
                heartbeat=True,
                expected_reads=reads,
            )

    # 4. Delegate to align.
    if need_extract:
        statistics = fastq_input_statistics(r1_path, r2_path)
        read_length = statistics.read_length if statistics is not None else None
    align_job = replace(
        job,
        r1=r1_path,
        r2=r2_path,
        input_reads=InputReads(reads, read_length),
        retire_fastqs=(r1_path, r2_path)
        if (
            extract_decision is not None
            and not job.keep_fastq
            and (extract_decision.run or extract_decision.metrics.get("temporary"))
        )
        else (),
    )
    if align_decision is None:
        align_decision = journal.decide(
            align_step, after=[extract_decision] if extract_decision else []
        )
    try:
        _align(journal, align_decision, lambda: align_cmd.run(align_job))
    except BaseException:
        if (
            need_extract
            and not job.keep_fastq
            and any(Path(path).exists() for path in (r1_path, r2_path))
        ):
            from wgsextract_cli.core.temp_directory import retain_temporary_directory

            directory = retain_temporary_directory()
            if directory:
                logging.warning(
                    "Alignment failed; extracted FASTQs are retained in %s. "
                    "Rerun the same command to reuse them, or set --r1 and --r2 to:\n"
                    "  %s\n  %s\n"
                    "Remove the retained workspace after recovery.",
                    directory,
                    r1_path,
                    r2_path,
                )
        raise

    # 5. Remove the FASTQs this workflow extracted unless asked to keep them
    # (now or by the run that extracted them). They are kept when alignment
    # fails so a rerun can reuse them.
    if extract_decision is None:
        return
    temporary = extract_decision.run or extract_decision.metrics.get("temporary")
    if temporary and not job.keep_fastq:
        for path in (r1_path, r2_path):
            try:
                Path(path).unlink(
                    missing_ok=current_result().metrics.get("alignment_mode")
                    == "staged_metal"
                )
            except OSError as exc:
                logging.warning("Could not remove %s: %s", path, exc)
        journal.retire("extract", [r1_path, r2_path])
    elif job.keep_fastq:
        current_result().add_output(r1_path, "fastq_r1")
        current_result().add_output(r2_path, "fastq_r2")
    else:  # kept by the --keep-fastq run that extracted them
        current_result().add_input(r1_path, "fastq_r1")
        current_result().add_input(r2_path, "fastq_r2")


def _extracted_alignment_needs(
    job: RealignJob,
    target: str | None,
    outdir: str,
    threads: str,
    memory: str,
    reads: int | None,
    read_length: float | None,
    minimap2: bool,
) -> _Needs:
    if minimap2:
        return _minimap2_needs(
            job,
            [job.input],
            target,
            threads,
            memory,
            reads=reads,
            samblaster=align_cmd.duplicate_marker(paired=True) == "samblaster",
        )
    return _report_resource_estimates(
        job,
        target,
        outdir,
        threads,
        memory,
        reads,
        input_paths=[job.input],
        need_extract=True,
        paired=True,
        read_length=read_length,
    )


def _recover_retained_fastqs(
    journal: StepJournal,
    extract_step: Step,
    staged: tuple[str, str],
    staging: str,
) -> tuple[Step, tuple[str, str]] | None:
    """Recover a private sibling workspace only after journal validation."""
    previous = journal.check(extract_step)
    if previous.metrics.get("temporary") is not True:
        return None
    try:
        retained = tuple(journal.resolve(output["path"]) for output in previous.outputs)
        resolved = tuple(path.resolve() for path in retained)
        workspace = Path(staging).resolve()
        if len(retained) != 2:
            return None
        sibling_workspace = (
            workspace.name.startswith(".wgse_run_")
            and resolved[0].parent == resolved[1].parent
            and resolved[0].parent.name.startswith(".wgse_run_")
            and resolved[0].parent.parent == workspace.parent
        )
        matching_names = tuple(path.name for path in resolved) == tuple(
            Path(path).name for path in staged
        )
        if not sibling_workspace or not matching_names:
            return None
        if sys.platform != "win32":
            # Windows temporary-directory access is controlled by its OS ACLs.
            directory, *files = [
                path.stat() for path in (resolved[0].parent, *resolved)
            ]
            private_owner = all(
                item.st_uid == os.geteuid() and not item.st_mode & 0o022
                for item in (directory, *files)
            )
            regular_pair = stat.S_ISDIR(directory.st_mode) and all(
                stat.S_ISREG(item.st_mode) for item in files
            )
            root = workspace.parent.stat()
            trusted_root = root.st_uid in (0, os.geteuid()) and (
                not root.st_mode & 0o022 or bool(root.st_mode & stat.S_ISVTX)
            )
            if not private_owner or not regular_pair or not trusted_root:
                return None
    except OSError:
        return None
    recovered = replace(extract_step, outputs=tuple(str(path) for path in retained))
    if journal.check(recovered).run:
        return None
    return recovered, (str(retained[0]), str(retained[1]))
