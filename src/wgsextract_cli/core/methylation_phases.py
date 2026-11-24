"""Verify phase-set coherence around targeted CpGs."""

import csv
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.methylation_annotations import Chromosome, Region
from wgsextract_cli.core.utils import UsageError

_REFERENCE_CIGAR = re.compile(r"(\d+)[MDN=X]")


def reference_end(start: int, cigar: str) -> int:
    return start + sum(int(n) for n in _REFERENCE_CIGAR.findall(cigar))


class IntervalLookup:
    def __init__(self, intervals: Iterable[tuple[int, int, int]]) -> None:
        self.intervals = sorted(intervals)
        self.starts: list[int] = []
        self.max_ends: list[int] = []
        end = 0
        for start, stop, _ in self.intervals:
            end = max(end, stop)
            self.starts.append(start)
            self.max_ends.append(end)

    def overlapping(self, start: int, stop: int) -> Iterator[tuple[int, int, int]]:
        first = bisect_right(self.max_ends, start)
        last = bisect_left(self.starts, stop, lo=first)
        for i in range(first, last):
            interval = self.intervals[i]
            if interval[1] > start:
                yield interval


def read_phase_blocks(
    path: Path, aliases: Mapping[str, Chromosome]
) -> dict[str, IntervalLookup]:
    """Load vcf phase's 1-based inclusive first/last positions as BED bounds."""
    intervals: dict[str, list[tuple[int, int, int]]] = {}
    seen: set[tuple[str, int]] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle, delimiter="\t")
        if not {"contig", "phase_set", "first", "last"} <= set(rows.fieldnames or []):
            raise UsageError(f"{path} is not a phased.blocks.tsv from vcf phase.")
        for row in rows:
            if (chrom := aliases.get(row["contig"])) is None:
                continue
            try:
                phase_set = int(row["phase_set"])
                first, last = int(row["first"]), int(row["last"])
                key = chrom.name, phase_set
                if (
                    not 1 <= first <= last <= chrom.length
                    or phase_set < 1
                    or key in seen
                ):
                    raise ValueError
            except (TypeError, ValueError) as error:
                raise UsageError(
                    f"Invalid phase block in {path}, line {rows.line_num}."
                ) from error
            seen.add(key)
            intervals.setdefault(chrom.name, []).append((first - 1, last, phase_set))
    return {chrom: IntervalLookup(bounds) for chrom, bounds in intervals.items()}


@dataclass
class PhaseEvidence:
    phase_set: int | None = None
    missing_ps: bool = False
    mixed_ps: bool = False

    def add(self, phase_set: int | None) -> None:
        if phase_set is None:
            self.missing_ps = True
        elif self.phase_set is None:
            self.phase_set = phase_set
        elif phase_set != self.phase_set:
            self.mixed_ps = True

    def classify(
        self, region: Region, blocks: Mapping[str, IntervalLookup] | None
    ) -> tuple[str, int | None]:
        if region.start is None or region.end is None:
            return "no_transcript", None
        index = blocks.get(region.chrom) if blocks is not None else None
        overlaps = list(index.overlapping(region.start, region.end)) if index else []
        if self.mixed_ps or len(overlaps) > 1:
            return "cross_block", None
        if self.missing_ps:
            return "missing_ps", None
        if self.phase_set is None:
            return "no_phased_reads", None
        if blocks is None:
            return "single_ps", self.phase_set
        if not overlaps:
            return "outside_block", None
        start, end, phase_set = overlaps[0]
        if phase_set != self.phase_set:
            return "phase_set_mismatch", None
        if start > region.start or end < region.end:
            return "outside_block", None
        return "single_block", phase_set
