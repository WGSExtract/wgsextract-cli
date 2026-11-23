"""GRCh38 interval tracks used by fastVEP's research ACMG classifier."""

import gzip
from collections.abc import Iterator
from itertools import chain
from pathlib import Path
from typing import Any

from wgsextract_cli.core import read_scope
from wgsextract_cli.core.download_progress import CancelEvent
from wgsextract_cli.core.utils import WGSExtractError

REGION_FLAGS = ("lcr", "segdup")


def repeatmasker_bed(source: Path, bed: Path, chromosomes: tuple[str, ...]) -> None:
    """Keep simple repeats and low complexity, not all interspersed repeats."""
    count = 0
    with gzip.open(source, "rt") as rows, bed.open("w", newline="\n") as out:
        for line in rows:
            cols = line.split("\t")
            if len(cols) < 17:
                raise WGSExtractError(f"{source.name} isn't a UCSC rmsk table.")
            if cols[5] in chromosomes and cols[11] in {
                "Simple_repeat",
                "Low_complexity",
            }:
                start, end = _interval(cols[6], cols[7], source)
                out.write(f"{cols[5]}\t{start}\t{end}\n")
                count += 1
    if not count:
        raise WGSExtractError(f"No primary chromosome simple repeats in {source.name}.")


def region_bed(
    source: Path, bed: Path, flag: str, chromosomes: tuple[str, ...]
) -> None:
    """Convert the gnomAD resource as its GRCh38 Hail importer reads it."""
    rows = []
    with source.open() as stream:
        for line in stream:
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split()
            if len(cols) < 3:
                raise WGSExtractError(f"{source.name} isn't a genomic interval table.")
            chrom, left, right = cols[:3]
            if chrom not in chromosomes:
                continue
            # gnomAD uses import_locus_intervals (1-based, closed) for LCR
            # but import_bed (0-based, half-open) for segmental duplications.
            if flag == "lcr" and left == "0":
                continue  # gnomAD's importer skips invalid GRCh38 intervals
            start, end = _interval(left, right, source, closed=flag == "lcr")
            rows.append((chrom, start, end))
    if not rows:
        raise WGSExtractError(f"No primary chromosome intervals in {source.name}.")
    with bed.open("w", newline="\n") as out:
        for chrom, start, end in sorted(rows):
            out.write(f"{chrom}\t{start}\t{end}\n")


def _interval(
    left: str, right: str, source: Path, *, closed: bool = False
) -> tuple[int, int]:
    try:
        start, end = int(left) - int(closed), int(right)
        if 0 <= start < end:
            return start, end
    except ValueError:
        pass
    raise WGSExtractError(f"Invalid interval {left}-{right} in {source.name}.")


def region_tracks(folder: Path, manifest: dict[str, Any]) -> dict[str, str]:
    """Hashes of the available, indexed region tracks, for cache and provenance."""
    found = {}
    for flag in REGION_FLAGS:
        entry = manifest.get("databases", {}).get(flag, {})
        bed = folder / region_name(flag)
        index = bed.with_name(bed.name + ".tbi")
        if (
            entry.get("file") == bed.name
            and all(read_scope.may_follow(p) and p.is_file() for p in (bed, index))
            and bed.stat().st_size == entry.get("bytes")
            and index.stat().st_size == entry.get("index_bytes")
            and isinstance(entry.get("sha256"), str)
        ):
            found[flag] = entry["sha256"]
    return found


def region_name(flag: str) -> str:
    return f"gnomad_{flag}.bed.gz"


def flags_at_sites(
    source: Iterator[bytes],
    tracks: dict[str, list[tuple[int, int]]],
    cancel: CancelEvent | None,
) -> Iterator[bytes]:
    """Join a sorted single-chromosome sites VCF on POS like the gnomAD locus lookup."""
    flags = {flag.encode(): iter(intervals) for flag, intervals in tracks.items()}
    header_prefixes = {b"##INFO=<ID=" + flag + b"," for flag in flags}
    for line in source:
        if line.startswith(b"##"):
            header_prefixes.discard(line.split(b",", 1)[0] + b",")
        elif line.startswith(b"#CHROM"):
            for prefix in sorted(header_prefixes):
                yield (
                    prefix
                    + b'Number=0,Type=Flag,Description="gnomAD GRCh38 region flag">\n'
                )
        else:
            break
        yield line
    else:
        return
    # Keep one interval for each mask. The source and each mask are sorted,
    # so even a long run only advances past each interval once.
    cursor = {flag: next(rows, None) for flag, rows in flags.items()}
    records = chain((line,), source)
    for n, line in enumerate(records):
        if cancel and n % 4096 == 0 and cancel.is_set():
            raise WGSExtractError("Cancelled.")
        if not any(cursor.values()):
            yield line
            continue
        pos = int(line.split(b"\t", 2)[1]) - 1
        hit = []
        for flag, interval in cursor.items():
            while interval is not None and interval[1] <= pos:
                interval = cursor[flag] = next(flags[flag], None)
            if interval is not None and interval[0] <= pos:
                hit.append(flag)
        if hit:
            yield _set_flags(line, hit)
        else:
            yield line


def _set_flags(line: bytes, hit: list[bytes]) -> bytes:
    cols = line.rstrip(b"\r\n").split(b"\t", 8)
    info = [] if cols[7] == b"." else cols[7].split(b";")
    cols[7] = b";".join([*info, *(flag for flag in hit if flag not in info)])
    return b"\t".join(cols) + b"\n"
