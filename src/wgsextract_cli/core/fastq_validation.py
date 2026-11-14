"""Exact streaming FASTQ validation before publishing an alignment."""

import gzip
from collections.abc import Iterator
from itertools import zip_longest

from wgsextract_cli.core.read_counts import FastqStatistics
from wgsextract_cli.core.utils import WGSExtractError


def _records(path: str) -> Iterator[tuple[bytes, int]]:
    opener = gzip.open if path.lower().endswith(".gz") else open
    try:
        with opener(path, "rb") as stream:
            while header := stream.readline():
                sequence = stream.readline().rstrip(b"\r\n")
                plus = stream.readline()
                quality = stream.readline().rstrip(b"\r\n")
                name = header[1:].split()
                if (
                    not header.startswith(b"@")
                    or not name
                    or not sequence
                    or not plus.startswith(b"+")
                    or len(sequence) != len(quality)
                ):
                    raise WGSExtractError(f"Invalid or truncated FASTQ: {path}")
                token = name[0]
                if token.endswith((b"/1", b"/2")):
                    token = token[:-2]
                yield token, len(sequence)
    except (OSError, EOFError) as error:
        raise WGSExtractError(f"Cannot read complete FASTQ {path}: {error}") from error


def validate_fastq(r1: str, r2: str | None = None) -> FastqStatistics:
    """Count complete reads, checking every paired name and gzip trailer."""
    reads = bases = 0
    if r2 is None:
        for _, length in _records(r1):
            reads += 1
            bases += length
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
