"""Nucleus-style region selections (DeepVariant's --regions) as merged intervals."""

import gzip
import hashlib
import re
from pathlib import Path

Intervals = dict[str, list[tuple[int, int]]]


def literal_region(selection: str, lengths: dict[str, int]) -> tuple[str, int, int]:
    if selection in lengths:
        return selection, 0, lengths[selection]
    match = re.match(r"^(\S+):([0-9,]+)-([0-9,]+)$", selection)
    if match:
        chrom, start_text, end_text = match.groups()
        start, end = int(start_text.replace(",", "")), int(end_text.replace(",", ""))
    else:
        match = re.match(r"^(\S+):([0-9,]+)$", selection)
        if not match:
            raise ValueError(f"Invalid interval: {selection}")
        chrom, position = match.groups()
        start = end = int(position.replace(",", ""))
    if start < 1 or end < start:
        raise ValueError(f"Invalid interval: {selection}")
    return chrom, start - 1, end


def bed_intervals(path: str) -> list[tuple[str, int, int]]:
    """Read zero-based, half-open intervals from BED, BED.gz or BEDPE."""
    lower = path.lower()
    opener = gzip.open if lower.endswith(".gz") else open
    rows = []
    with opener(path, "rt") as source:
        for line in source:
            fields = line.split()
            if (
                not fields
                or fields[0].startswith("#")
                or fields[0] in {"track", "browser"}
            ):
                continue
            try:
                if lower.endswith(".bedpe"):
                    if fields[0] != fields[3]:
                        continue
                    chrom, start, end = fields[0], int(fields[1]), int(fields[5])
                else:
                    chrom, start, end = fields[0], int(fields[1]), int(fields[2])
            except (IndexError, ValueError) as error:
                raise ValueError(f"Invalid interval: {line.strip()}") from error
            if start < 0 or end < start:
                raise ValueError(f"Invalid interval: {line.strip()}")
            rows.append((chrom, start, end))
    return rows


def is_region_file(region: str | None) -> bool:
    """Whether a ``--region`` value names a file (a BED) rather than regions.

    A long region list is no path: ``Path.is_file`` raises ENAMETOOLONG on it."""
    if not region:
        return False
    try:
        return Path(region).is_file()
    except OSError:
        return False


_MAX_REGION_LABEL = 100  # characters of a --region list kept in a file name


def region_output_suffix(region: str) -> str:
    """The file-name suffix ``extract custom`` gives a ``--region``: a BED's
    name, else the regions with ``:`` and ``,`` replaced; a list too long for a
    file name becomes ``<N>regions-<sha256 prefix>`` (#977)."""
    if is_region_file(region):
        return Path(region).name.replace(":", "_").replace(",", "_")
    suffix = region.replace(":", "_").replace(",", "_")
    if len(suffix) <= _MAX_REGION_LABEL:
        return suffix
    digest = hashlib.sha256(region.encode()).hexdigest()[:16]
    return f"{len(region_items(region, commas=True))}regions-{digest}"


def region_items(region: str, *, commas: bool, files: bool = True) -> list[str]:
    """The regions and BED files in a ``--region`` list: GATK and Parabricks
    separate them with commas, DeepVariant on the CPU with whitespace."""
    if files and is_region_file(region):
        return [region]
    if commas:
        return [item.strip() for item in region.split(",") if item.strip()]
    return region.split()


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union overlapping or touching half-open ranges on one contig."""
    union: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if union and start <= union[-1][1]:
            union[-1] = (union[-1][0], max(union[-1][1], end))
        else:
            union.append((start, end))
    return union


def selected_intervals(
    region: str, lengths: dict[str, int], *, commas: bool = False
) -> Intervals:
    """Union of whitespace- (or, with ``commas``, comma-) separated literals
    and BED/BED.gz/BEDPE files.

    Rows on contigs absent from ``lengths`` are dropped and intervals are
    clipped to the contig; raises ValueError when nothing remains.
    """
    intervals: Intervals = {}
    for selection in region_items(region, commas=commas):
        if selection.lower().endswith((".bed", ".bed.gz", ".bedpe")):
            rows = bed_intervals(selection)
        else:
            rows = [literal_region(selection, lengths)]
        for chrom, start, end in rows:
            if chrom not in lengths:
                continue
            start, end = min(start, lengths[chrom]), min(end, lengths[chrom])
            if end > start:
                intervals.setdefault(chrom, []).append((start, end))
    if not intervals:
        raise ValueError("Selection covers no reference bases.")
    return merge_intervals(intervals)


def merge_intervals(intervals: Intervals) -> Intervals:
    """Union each contig's intervals, in ascending coordinate order."""
    return {chrom: merge_ranges(ranges) for chrom, ranges in intervals.items()}
