"""Select the samples' records a targeted annotation chain needs."""

import re
import tempfile
from pathlib import Path

from wgsextract_cli.core.region_selection import (
    Intervals,
    bed_intervals,
    merge_intervals,
)
from wgsextract_cli.core.utils import UsageError, run_command
from wgsextract_cli.core.variant_files import (
    get_bam_header,
    match_chromosome_name,
    vcf_header_chromosomes,
    vcf_index_chromosomes,
)

_CONTIG_END = 2**31 - 1


def vcf_indexes(path: str) -> list[Path]:
    """Existing index sidecars, including HTSlib's alternate compressed names."""
    source = Path(path)
    candidates = [Path(path + ext) for ext in (".tbi", ".csi")]
    if source.suffix.lower() in {".gz", ".bgz"}:
        candidates.extend(source.with_suffix(ext) for ext in (".tbi", ".csi"))
    return [index for index in candidates if index.is_file()]


def _region_rows(region: str, contigs: list[str]) -> list[tuple[str, int, int]]:
    rows = []
    for part in region.split(","):
        item = part.strip()
        chrom, colon, span = item.rpartition(":")
        if not colon or match_chromosome_name(item, contigs):
            if not item or any(ch.isspace() for ch in item):
                raise UsageError(f"Invalid --region: {item!r}")
            rows.append((item, 0, _CONTIG_END))
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d*))?|-(\d+)", span)
        if not match or not chrom:
            raise UsageError(f"Invalid --region: {item!r}")
        first, last, leading_dash = match.groups()
        start = int(first or "1")
        end = (
            int(leading_dash)
            if leading_dash
            else start
            if last is None
            else int(last or str(_CONTIG_END))
        )
        if not 0 < start <= end:
            raise UsageError(f"Invalid --region: {item!r}")
        rows.append((chrom, start - 1, end))
    return rows


def _selection(
    region: str | None, bed: Path | None, contigs: list[str], *, whole_contigs: bool
) -> Intervals:
    if bed is not None:
        try:
            rows = bed_intervals(str(bed))
        except (OSError, ValueError, EOFError) as exc:
            raise UsageError(f"Invalid --bed {bed}: {exc}") from exc
    else:
        assert region is not None
        rows = _region_rows(region, contigs)
    selected: Intervals = {}
    names: dict[str, str | None] = {}
    for chrom, start, end in rows:
        if chrom not in names:
            names[chrom] = match_chromosome_name(chrom, contigs) if contigs else chrom
        name = names[chrom]
        if name is not None and start < end:
            if whole_contigs:
                selected[name] = [(0, _CONTIG_END)]
            else:
                selected.setdefault(name, []).append((start, end))
    merged = selected if whole_contigs else merge_intervals(selected)
    return {name: merged[name] for name in contigs or merged if name in merged}


def _write_regions(path: Path, intervals: Intervals) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for chrom, ranges in intervals.items():
            stream.writelines(f"{chrom}\t{start + 1}\t{end}\n" for start, end in ranges)


def select_records(
    source: str,
    output: str,
    region: str | None,
    bed: Path | None,
    threads: str,
    *,
    whole_contigs: bool,
) -> None:
    indexes = vcf_indexes(source)
    indexed = bool(indexes) and all(
        p.stat().st_mtime_ns >= Path(source).stat().st_mtime_ns for p in indexes
    )
    from_index = vcf_index_chromosomes(source) if indexed else []
    declared = vcf_header_chromosomes(get_bam_header(source))
    contigs = list(dict.fromkeys([*from_index, *declared]))
    intervals = _selection(region, bed, contigs, whole_contigs=whole_contigs)
    command = ["bcftools", "view", "--no-version", "--threads", str(int(threads) - 1)]
    if not intervals:
        run_command([*command, "-h", "-Oz", "-o", output, source])
        return

    with tempfile.TemporaryDirectory(dir=Path(output).parent) as work:
        path = Path(work, "targets.tsv")
        _write_regions(path, intervals)
        if indexed and len(from_index) > 0:
            # One seek per contig prevents a long record spanning separate BED
            # intervals from being returned twice. -T applies the exact union.
            bounds = Path(work, "bounds.tsv")
            _write_regions(
                bounds,
                {
                    chrom: [(rows[0][0], rows[-1][1])]
                    for chrom, rows in intervals.items()
                },
            )
            command += ["-R", str(bounds)]
        command += ["-T", str(path), "--targets-overlap", "1"]
        run_command([*command, "-Oz", "-o", output, source])
