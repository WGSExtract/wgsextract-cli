"""Shared streamed alignment writing, indexing and output validation."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from wgsextract_cli.commands._extract_helpers import cleanup_files
from wgsextract_cli.core.alignment.fastq import check_primary_reads
from wgsextract_cli.core.alignment.profiles import output_profile_options
from wgsextract_cli.core.atomic_output import atomic_outputs_enabled
from wgsextract_cli.core.output_checks import record_alignment_output
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.samtools_commands import indexed_output, written_index
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import (
    get_sam_index_cmd,
    get_sam_sort_cmd,
    run_command,
    run_pipeline,
)


def record_alignment(out_path: str, aligner: str, ref: str) -> None:
    """Register an aligner's BAM/CRAM and its record counts on the result."""
    current_result().set_reference(ref)
    counts = record_alignment_output(
        out_path, hint="check that the input reads are not empty."
    )
    current_result().add_metrics(aligner=aligner, **counts)


def _sort_and_mark_stages(
    out_path: str,
    *,
    threads: str,
    memory: str,
    fmt: str,
    ref: str,
    output_profile: str = "default",
) -> list[list[str]]:
    """samtools fixmate | sort | markdup, writing and indexing out_path. The
    sorted stream goes straight into markdup uncompressed, so the output is
    written once. fixmate reads the aligner's output directly: it keeps mates
    together."""
    directory = configured_temp_directory()
    tmp = str(Path(directory, "align")) if directory else f"{out_path}.tmp"
    out_fmt = ["-O", "CRAM", "--reference", ref] if fmt == "CRAM" else ["-O", "BAM"]
    if fmt == "CRAM":
        out_fmt += output_profile_options(output_profile)
    return [
        ["samtools", "fixmate", "-m", "-u", "-", "-"],
        ["samtools", "sort", "-@", threads, "-m", memory, "-u", "-T", tmp, "-"],
        ["samtools", "markdup", "-@", threads, "-T", tmp, *out_fmt, "--write-index",
         "-", indexed_output(out_path)],
    ]  # fmt: skip


def _index_output(path: str, threads: str = "1") -> None:
    command = get_sam_index_cmd(path, threads=threads)
    if atomic_outputs_enabled():
        from wgsextract_cli.core.atomic_output import atomic_output

        suffix = ".crai" if path.lower().endswith(".cram") else ".bai"
        with atomic_output(path + suffix, retire_indexes=False) as staged:
            run_command([*command, staged])
    else:
        run_command(command)


def run_streamed_alignment(
    align_cmd: Sequence[object],
    out_path: str,
    *,
    threads: str,
    memory: str,
    fmt: str,
    ref: str,
    duplicates: str | None,
    upstream: list[Sequence[object]] | None = None,
    expected_reads: int | None = None,
    exact_reads: int | None = None,
    staged_mode: bool = False,
    retire_fastqs: Sequence[str] = (),
    read_length: float | None = None,
    output_profile: str = "default",
    compression_threads: str | None = None,
) -> None:
    """Run the aligner (after any upstream stages feeding it), mark duplicates
    (with duplicate_marker's choice, or not at all for long reads: None), sort,
    and write the output with its index (written_index): the last samtools
    stage indexes as it writes, which saves reading the whole output again."""
    from wgsextract_cli.core.atomic_output import safe_output

    partial = [out_path, written_index(out_path)]
    if staged_mode:
        from wgsextract_cli.core.staged_alignment import run_staged_alignment

        workspace = None
        try:
            with cleanup_files(
                on_failure=partial, preserve_outputs=atomic_outputs_enabled()
            ):
                with safe_output(out_path) as staged:
                    workspace = run_staged_alignment(
                        [*(upstream or []), list(align_cmd)],
                        out_path,
                        staged,
                        memory=memory,
                        threads=threads,
                        compression_threads=compression_threads,
                        fmt=fmt,
                        ref=ref,
                        retire_fastqs=retire_fastqs,
                        read_length=read_length,
                        expected_reads=expected_reads,
                        output_profile=output_profile,
                    )
                    if exact_reads is not None:
                        check_primary_reads(staged, exact_reads, ref)
        except BaseException:
            if workspace is not None:
                warn(
                    "staged_alignment_retained",
                    f"Staged checkpoints retained in {workspace}; rerun unchanged inputs to resume.",
                    workspace=str(workspace),
                )
            raise
        try:
            shutil.rmtree(workspace)
        except OSError as error:
            logging.warning(
                "Could not remove staged workspace %s: %s", workspace, error
            )
        return
    with cleanup_files(on_failure=partial, preserve_outputs=atomic_outputs_enabled()):
        with safe_output(out_path) as staged:
            stages: list[Sequence[object]] = [*(upstream or []), list(align_cmd)]
            sort = get_sam_sort_cmd(
                staged,
                threads,
                memory,
                fmt=fmt,
                reference=ref,
                sam_input=True,
                write_index=True,
            )
            if fmt == "CRAM":
                sort += output_profile_options(output_profile)
            if duplicates == "samblaster":
                logging.info("Using samblaster for marking duplicates...")
                stages += [["samblaster"], sort]
            elif duplicates == "markdup":
                logging.info("Marking duplicates with samtools markdup...")
                stages += _sort_and_mark_stages(
                    staged,
                    threads=threads,
                    memory=memory,
                    fmt=fmt,
                    ref=ref,
                    output_profile=output_profile,
                )
            else:
                stages.append(sort)
            run_pipeline(
                stages,
                description="Alignment",
                heartbeat=True,
                expected_reads=expected_reads,
            )
            if exact_reads is not None:
                check_primary_reads(staged, exact_reads, ref)


def _ensure_read_group_sample(path: str, sample: str, ref: str) -> None:
    """Add SM:<sample> to read groups without one (header only, in place)."""
    cram = ["-T", ref] if path.endswith(".cram") else []
    header = run_command(
        ["samtools", "view", "-H", *cram, path], capture_output=True
    ).stdout
    lines = header.splitlines()
    fixed = [
        line + f"\tSM:{sample}"
        if line.startswith("@RG") and "\tSM:" not in line
        else line
        for line in lines
    ]
    if fixed == lines:
        return
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sam", dir=str(Path(path).parent), delete=False
    ) as new_header:
        new_header.write("\n".join(fixed) + "\n")
    try:
        if path.endswith(".cram"):
            run_command(["samtools", "reheader", "-i", new_header.name, path])
        else:
            rewritten = path + ".reheader"
            with open(rewritten, "wb") as out:
                run_command(["samtools", "reheader", new_header.name, path], stdout=out)
            os.replace(rewritten, path)
            _index_output(path)
    finally:
        Path(new_header.name).unlink(missing_ok=True)
    logging.info(f"Added the sample name {sample} to the read groups of {path}.")
