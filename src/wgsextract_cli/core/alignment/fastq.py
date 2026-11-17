"""Exact streaming FASTQ validation before publishing an alignment."""

import gzip
import os
import select
import tempfile
import zlib
from collections.abc import Iterator
from contextlib import ExitStack, nullcontext
from itertools import zip_longest
from pathlib import Path

from wgsextract_cli.core.read_counts import FastqStatistics
from wgsextract_cli.core.utils import WGSExtractError


def stage_read_input(path: str, directory: str, cleanup: ExitStack) -> str:
    """Spool a one-shot read stream so validation cannot consume mapper input."""
    from wgsextract_cli.api._runtime import checkpoint, report

    if Path(path).is_file():
        return path
    checkpoint()
    work = cleanup.enter_context(
        tempfile.TemporaryDirectory(prefix=".wgse_reads_", dir=directory)
    )
    suffix = "".join(Path(path).suffixes) or ".fq"
    staged = Path(work, f"reads{suffix}")
    try:
        report("reads_staging", f"Staging read stream {path}.")
        checkpoint()
        source = (
            os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb", buffering=0)
            if os.name == "posix"
            else open(path, "rb", buffering=0)
        )
        with source, staged.open("wb") as target:
            while True:
                checkpoint()
                if os.name == "posix" and not select.select([source], [], [], 0.1)[0]:
                    continue
                chunk = source.read(1024 * 1024)
                if chunk is None:
                    continue
                if not chunk:
                    break
                target.write(chunk)
        with staged.open("rb") as sniff:
            compressed = sniff.read(2) == b"\x1f\x8b"
        if compressed and not staged.name.endswith(".gz"):
            renamed = staged.with_name(staged.name + ".gz")
            staged.rename(renamed)
            staged = renamed
    except OSError as error:
        raise WGSExtractError(f"Cannot stage read input {path}: {error}") from error
    return str(staged)


def fastq_input(path: str) -> bool:
    """FASTA and FASTQ are distinguished by content, not filename suffix."""
    if not Path(path).is_file():
        raise WGSExtractError(f"Read input must be a regular file: {path}")
    try:
        with open(path, "rb") as raw:
            compressed = raw.read(2) == b"\x1f\x8b"
            raw.seek(0)
            with (
                gzip.GzipFile(fileobj=raw) if compressed else nullcontext(raw) as stream
            ):
                return stream.read(1) != b">"
    except (OSError, EOFError, zlib.error) as error:
        raise WGSExtractError(f"Cannot read complete FASTQ {path}: {error}") from error


def _records(path: str) -> Iterator[tuple[bytes, int]]:
    from wgsextract_cli.api._runtime import checkpoint

    if not Path(path).is_file():
        raise WGSExtractError(f"FASTQ input must be a regular file: {path}")
    try:
        with open(path, "rb") as raw:
            compressed = raw.read(2) == b"\x1f\x8b"
            raw.seek(0)
            with (
                gzip.GzipFile(fileobj=raw) if compressed else nullcontext(raw) as stream
            ):
                while header := stream.readline():
                    checkpoint()
                    name = header[1:].split()
                    if not header.startswith(b"@") or not name:
                        raise WGSExtractError(f"Invalid or truncated FASTQ: {path}")
                    bases = 0
                    while line := stream.readline():
                        checkpoint()
                        if line.startswith(b"+"):
                            break
                        bases += len(line.rstrip(b"\r\n"))
                    if not line or not bases:
                        raise WGSExtractError(f"Invalid or truncated FASTQ: {path}")
                    qualities = 0
                    while qualities < bases and (line := stream.readline()):
                        checkpoint()
                        qualities += len(line.rstrip(b"\r\n"))
                    if qualities != bases:
                        raise WGSExtractError(f"Invalid or truncated FASTQ: {path}")
                    token = name[0]
                    if token.endswith((b"/1", b"/2")):
                        token = token[:-2]
                    yield token, bases
    except (OSError, EOFError, zlib.error) as error:
        raise WGSExtractError(f"Cannot read complete FASTQ {path}: {error}") from error


def validate_fastq(r1: str, r2: str | None = None) -> FastqStatistics:
    """Count complete reads, checking every paired name and gzip trailer."""
    from wgsextract_cli.api._runtime import checkpoint, report

    reads = bases = 0
    if r2 is None:
        for _, length in _records(r1):
            reads += 1
            bases += length
            if reads % 1024 == 0:
                report("reads_validation", f"Validated {reads:,} reads.")
                checkpoint()
    else:
        for number, (left, right) in enumerate(
            zip_longest(_records(r1), _records(r2)), 1
        ):
            if left is None or right is None:
                raise WGSExtractError(
                    f"Paired FASTQ read count mismatch at pair {number}: {r1}, {r2}"
                )
            if left[0] != right[0]:
                raise WGSExtractError(
                    f"Paired FASTQ read name mismatch at pair {number}: {r1}, {r2}"
                )
            reads += 2
            bases += left[1] + right[1]
            if reads % 1024 == 0:
                report("reads_validation", f"Validated {reads:,} reads.")
                checkpoint()
    return FastqStatistics(reads, bases / reads if reads else None)


def check_primary_reads(path: str, expected: int, ref: str) -> None:
    from wgsextract_cli.core.results import current_result
    from wgsextract_cli.core.utils import run_command

    result = run_command(
        ["samtools", "view", "-c", "-F", "0x900", "-T", ref, path],
        capture_output=True,
    )
    observed = int(result.stdout.strip())
    if observed != expected:
        raise WGSExtractError(
            f"Alignment read count mismatch: {expected} input reads, "
            f"{observed} primary output records. Output was not published."
        )
    current_result().add_metrics(input_reads=expected, primary_records=observed)
