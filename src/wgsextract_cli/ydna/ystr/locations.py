"""Sequence-based reference placements used by the Y-STR panel generator."""

from __future__ import annotations

import re
from collections.abc import Iterator

KMER = 14
CONTEXT = 40
ANCHOR_SHIFTS = (0, 8, 20, 40, 80)


def revcomp(seq: str) -> str:
    return seq[::-1].translate(str.maketrans("ACGTN", "TGCAN"))


def unique_find(haystack: str, needle: str) -> int | None:
    first = haystack.find(needle)
    if first == -1 or haystack.find(needle, first + 1) != -1:
        return None
    return first


def mismatches(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b, strict=True)) + abs(len(a) - len(b))


def locate_all(ref38: str, s: int, e: int, other: str) -> list[tuple[int, int, str]]:
    """Paired homologous anchors, in reference coordinates and orientation."""
    flank_l, flank_r = ref38[s - KMER : s], ref38[e : e + KMER]
    locations: set[tuple[int, int, str]] = set()
    for shift in ANCHOR_SHIFTS:
        for context in (CONTEXT, 30, 22):
            left = ref38[s - KMER - context - shift : s - shift]
            right = ref38[e + shift : e + KMER + context + shift]
            if len(left) != KMER + context or len(right) != KMER + context:
                continue
            for strand, anchor_l, anchor_r in (
                ("+", left, right),
                ("-", revcomp(right), revcomp(left)),
            ):
                anchors = list(re.finditer(re.escape(anchor_l), other))
                for index, match in enumerate(anchors):
                    lo = match.end() + shift
                    hi = lo + (e - s) + 400 + len(anchor_r) + shift
                    if index + 1 < len(anchors):
                        # Never pair anchors from different neighbouring copies.
                        hi = min(hi, anchors[index + 1].start())
                    for second in re.compile(re.escape(anchor_r)).finditer(
                        other, lo, min(len(other), hi)
                    ):
                        bs, be = lo, second.start() - shift
                        expected_l, expected_r = (
                            (flank_l, flank_r)
                            if strand == "+"
                            else (revcomp(flank_r), revcomp(flank_l))
                        )
                        if (
                            be >= bs
                            and be - bs < (e - s) + 400
                            and mismatches(other[bs - KMER : bs], expected_l) <= 1
                            and mismatches(other[be : be + KMER], expected_r) <= 1
                        ):
                            locations.add((bs, be, strand))
    return sorted(locations)


def strand_placements(
    ref38: str, s: int, e: int, other: str
) -> Iterator[tuple[int, int, str]]:
    """Yield the strongest globally unique anchor pair on each strand."""
    flank_l, flank_r = ref38[s - KMER : s], ref38[e : e + KMER]

    def place(strand: str) -> tuple[int, int, str] | None:
        for shift in ANCHOR_SHIFTS:
            for context in (CONTEXT, 30, 22):
                left = ref38[s - KMER - context - shift : s - shift]
                right = ref38[e + shift : e + KMER + context + shift]
                if len(left) != KMER + context or len(right) != KMER + context:
                    continue
                anchor_l, anchor_r = (
                    (left, right) if strand == "+" else (revcomp(right), revcomp(left))
                )
                lpos = unique_find(other, anchor_l)
                rpos = unique_find(other, anchor_r)
                if lpos is None or rpos is None:
                    continue
                bs, be = lpos + len(anchor_l) + shift, rpos - shift
                expected_l, expected_r = (
                    (flank_l, flank_r)
                    if strand == "+"
                    else (revcomp(flank_r), revcomp(flank_l))
                )
                if (
                    be >= bs
                    and be - bs < (e - s) + 400
                    and mismatches(other[bs - KMER : bs], expected_l) <= 1
                    and mismatches(other[be : be + KMER], expected_r) <= 1
                ):
                    return bs, be, strand
        return None

    for strand in ("+", "-"):
        if (placement := place(strand)) is not None:
            yield placement


def locate(ref38: str, s: int, e: int, other: str) -> tuple[int, int, str] | None:
    """A tract with a unique placement after considering both strands."""
    placements = list(strand_placements(ref38, s, e, other))
    return placements[0] if len(placements) == 1 else None
