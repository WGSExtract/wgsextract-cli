"""Conservative platform evidence from a bounded FASTQ prefix."""

import re
from functools import lru_cache
from statistics import median
from typing import NamedTuple

from wgsextract_cli.core.read_counts import _decoded_prefix


class FastqPlatform(NamedTuple):
    platform: str | None
    method: str
    statistics: dict[str, float]


@lru_cache(maxsize=128)
def fastq_platform(path: str, size: int, mtime: int, ctime: int) -> FastqPlatform:
    """Sample at most 4096 records, 4 MiB compressed / 16 MiB decoded.

    The revision arguments invalidate the cache when a file is replaced.
    No read identifiers, sequences or tag values are retained as provenance.
    """
    data, consumed, exact, _size = _decoded_prefix(path)
    lines = data.splitlines()
    if not exact and not data.endswith(b"\n"):
        lines.pop()
    lengths: list[int] = []
    qualities: list[float] = []
    high_quality = bases = ont = pacbio = pacbio_hifi = 0
    max_q = 0
    for offset in range(0, min(len(lines) // 4, 4096) * 4, 4):
        name, sequence, plus, quality = lines[offset : offset + 4]
        if (
            not name.startswith(b"@")
            or not plus.startswith(b"+")
            or not sequence
            or len(sequence) != len(quality)
            or min(quality) < 33
            or max(quality) > 126
        ):
            raise ValueError("not a complete four-line FASTQ record")
        lengths.append(len(sequence))
        qualities.append(sum(q - 33 for q in quality) / len(quality))
        bases += len(quality)
        high_quality += sum(q >= 63 for q in quality)
        max_q = max(max_q, max(quality) - 33)
        tags = set(re.findall(rb"(?:^|\s)([a-z_]+)=", name))
        ont += bool(
            tags & {b"runid", b"ch", b"start_time", b"basecall_model_version_id"}
        )
        pacbio += bool(tags & {b"np", b"rq"})
        values = dict(re.findall(rb"(?:^|\s)(np|rq)=([^\s]+)", name))
        try:
            if b"rq" in values:
                pacbio_hifi += float(values[b"rq"]) >= 0.99
            elif b"np" in values:
                pacbio_hifi += int(values[b"np"]) >= 2
        except ValueError:
            pass
    if not lengths:
        raise ValueError("no complete FASTQ sample")
    ordered = sorted(lengths, reverse=True)
    cumulative = 0
    n50 = ordered[0]
    for length in ordered:
        cumulative += length
        if cumulative * 2 >= bases:
            n50 = length
            break
    stats = {
        "sampled_reads": float(len(lengths)),
        "sampled_bytes": float(consumed),
        "median_length": float(median(lengths)),
        "n50_length": float(n50),
        "mean_q": sum(q * length for q, length in zip(qualities, lengths, strict=True))
        / bases,
        "max_q": float(max_q),
        "length_spread": max(lengths) / min(lengths),
    }
    if stats["median_length"] <= 300:
        return FastqPlatform(None, "FASTQ short-read length sample", stats)
    if ont and not pacbio:
        return FastqPlatform("ont", "FASTQ ONT header tags", stats)
    if pacbio and not ont and pacbio_hifi == pacbio:
        return FastqPlatform("pacbio", "FASTQ PacBio header tags", stats)
    # HiFi's capped high qualities are useful evidence after names/tags were
    # stripped. Low-quality broad-length data could be ONT *or* CLR: don't guess.
    if (
        not ont
        and not pacbio
        and stats["mean_q"] >= 30
        and stats["max_q"] > 50
        and high_quality / bases >= 0.9
        and stats["length_spread"] <= 3
    ):
        return FastqPlatform("pacbio", "FASTQ HiFi quality and length profile", stats)
    return FastqPlatform(None, "ambiguous long-read FASTQ sample", stats)
