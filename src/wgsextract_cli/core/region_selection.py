"""Nucleus-style region selections (DeepVariant's --regions) as merged intervals."""

import gzip
import hashlib
import io
import os
import re
from collections.abc import Iterable
from pathlib import Path

Intervals = dict[str, list[tuple[int, int]]]

_REGION_NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"
_REGION_SPAN = rf"(?:{_REGION_NUMBER}(?:-{_REGION_NUMBER}?)?|-{_REGION_NUMBER})"
_COMMA_REGION_FIELD = re.compile(rf"\s*[^,\s]+:{_REGION_SPAN}\s*(?=,|$)|[^,]+")
_COMMA = re.compile(r"\s*,\s*")


def comma_region_items(region: str) -> list[str]:
    """Split comma-separated literals without splitting grouped coordinates."""
    items: list[str] = []
    end = 0
    for match in _COMMA_REGION_FIELD.finditer(region):
        gap = region[end : match.start()]
        if (items and not _COMMA.fullmatch(gap)) or (not items and gap):
            raise ValueError("Empty interval in comma-separated selection.")
        part = match[0].strip()
        if not part:
            raise ValueError("Empty interval in comma-separated selection.")
        items.append(part)
        end = match.end()
    if not items or region[end:].strip():
        raise ValueError("Empty interval in comma-separated selection.")
    return items


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
    with opener(path, "rt") as source:
        return _parse_bed(source, bedpe=lower.endswith(".bedpe"))


def bed_intervals_and_sha256(path: Path) -> tuple[list[tuple[str, int, int]], str]:
    """Parse BED and hash its original bytes through the same file descriptor."""
    digest = hashlib.sha256()
    lower = path.name.lower()
    with path.open("rb") as raw:
        before = os.fstat(raw.fileno())
        while chunk := raw.read(1 << 20):
            digest.update(chunk)
        raw.seek(0)
        binary = gzip.GzipFile(fileobj=raw) if lower.endswith(".gz") else raw
        with io.TextIOWrapper(binary, encoding="utf-8") as source:
            rows = _parse_bed(source, bedpe=lower.endswith(".bedpe"))
            after = os.fstat(raw.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError(f"{path} changed while it was being read.")
    return rows, digest.hexdigest()


def _parse_bed(source: Iterable[str], *, bedpe: bool) -> list[tuple[str, int, int]]:
    rows = []
    for line in source:
        fields = line.split()
        if not fields or fields[0].startswith("#") or fields[0] in {"track", "browser"}:
            continue
        try:
            if bedpe:
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


def is_bed_selection(region: str) -> bool:
    return region.lower().endswith(
        (".bed", ".bed.gz", ".bedpe", ".bedpe.gz")
    ) or is_region_file(region)


def alignment_intervals(
    region: str, reference: dict[str, int], alignment: dict[str, int]
) -> Intervals:
    """Strict selection for callers: no unknown contigs or clipped intervals.

    Text ranges are 1-based inclusive; an omitted end extends to the end of
    that contig. BED uses 0-based half-open coordinates.
    """
    intervals: Intervals = {}
    if region.lower().endswith((".bedpe", ".bedpe.gz")):
        raise ValueError("BEDPE is not supported; provide a BED file.")
    if is_bed_selection(region):
        rows = bed_intervals(region)
    else:
        rows = []
        for part in comma_region_items(region):
            if any(
                piece in reference or piece in alignment
                for piece in part.split(",")[1:]
            ):
                raise ValueError(f"Ambiguous interval {part!r}; use a BED file.")
            contig, colon, span = part.rpartition(":")
            if (
                part in reference
                and contig in reference
                and re.fullmatch(_REGION_SPAN, span)
            ):
                raise ValueError(f"Ambiguous interval {part!r}; use a BED file.")
            if part in reference or part in alignment:
                _check_contig(part, reference, alignment)
            else:
                contig = contig if colon else part
                _check_contig(contig, reference, alignment)
                if not colon or not re.fullmatch(_REGION_SPAN, span):
                    raise ValueError(f"Invalid interval: {part}")
                first, _, last = span.partition("-")
                part = f"{contig}:{first or '1'}-{last or reference[contig]}"
            rows.append(literal_region(part, reference))
    for chrom, start, end in rows:
        _check_contig(chrom, reference, alignment)
        if start >= end or end > min(reference[chrom], alignment[chrom]):
            raise ValueError(
                f"Interval {chrom}:{start + 1}-{end} is empty or out of bounds."
            )
        intervals.setdefault(chrom, []).append((start, end))
    if not intervals:
        raise ValueError("Selection covers no reference bases.")
    return {chrom: merge_ranges(ranges) for chrom, ranges in intervals.items()}


def _check_contig(
    chrom: str, reference: dict[str, int], alignment: dict[str, int]
) -> None:
    if chrom not in reference:
        raise ValueError(f"Contig {chrom!r} is not in the reference.")
    if chrom not in alignment:
        raise ValueError(f"Contig {chrom!r} is not in the alignment header.")


def write_bed_intervals(path: Path, intervals: Intervals) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as destination:
        for chrom, ranges in intervals.items():
            for start, end in ranges:
                destination.write(f"{chrom}\t{start}\t{end}\n")


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
