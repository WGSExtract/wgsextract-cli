"""Shared gVCF record classification and interval resolution.

The public reference-evidence facade and Clair3's deletion-shadow classifier
share these states and parsing rules without depending on each other.
"""

from __future__ import annotations

import enum
import math
import re
import shlex
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from wgsextract_cli.core.variant_confidence import SYMBOLIC_REFERENCE_ALTS, Thresholds

GQ, DEPTH_ONLY = "gq", "depth_only"
Interval = tuple[str, int, int]  # contig, 1-based start, inclusive end


def bcftools_gvcf_header(header: str) -> bool:
    """Whether a recorded bcftools call command explicitly enabled gVCF."""
    for line in header.splitlines():
        if not line.startswith("##bcftools_callCommand="):
            continue
        try:
            args = shlex.split(line.split("=", 1)[1].partition("; Date=")[0])
        except ValueError:
            continue
        for index, arg in enumerate(args):
            if arg == "--":
                break
            if arg in ("-g", "--gvcf") and index + 1 < len(args):
                if re.fullmatch(r"\d+(?:,\d+)*", args[index + 1]):
                    return True
            if re.fullmatch(r"-g\d+(?:,\d+)*|--gvcf=\d+(?:,\d+)*", arg):
                return True
    return False


HEADER_KEY = "wgseReferenceEvidence"


def header_declares_gvcf(header: str) -> bool:
    """Recognize WGS Extract evidence and GATK-style reference confidence bands."""
    return any(
        line.startswith((f"##{HEADER_KEY}=", "##GVCFBlock"))
        for line in header.splitlines()
    )


class State(enum.Enum):
    """A base's evidence state, from the strongest claim against reference
    to the weakest. Where records overlap, the earlier state wins."""

    EXCLUDED_BY_POLICY = "excluded_by_policy"
    VARIANT = "variant"
    LOW_QUALITY_VARIANT = "low_quality_variant"
    FILTERED = "filtered"
    NO_CALL = "no_call"
    LOW_QUALITY_REFERENCE = "low_quality_reference"
    UNCOVERED = "uncovered"
    CALLABLE_REFERENCE = "callable_reference"
    NOT_ASSESSED = "not_assessed"


_RANK = {state: rank for rank, state in enumerate(State)}
Classified = tuple[str, int, int, State]  # a record's bases and their state


@dataclass(frozen=True)
class Span:
    """Consecutive bases of one contig in one state (1-based, inclusive)."""

    contig: str
    start: int
    end: int
    state: State

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def count_bases(spans: Iterable[Span]) -> Counter[State]:
    """Bases per state."""
    counts: Counter[State] = Counter()
    for span in spans:
        counts[span.state] += span.length
    return counts


def _info(text: str) -> dict[str, str]:
    return dict(item.partition("=")[::2] for item in text.split(";") if item != ".")


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "", ".") else None
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    try:
        number = float(value) if value not in (None, "", ".") else None
        return number if number is not None and math.isfinite(number) else None
    except ValueError:
        return None


def _sample(fields: Sequence[str]) -> dict[str, str]:
    keys = fields[8].split(":") if len(fields) > 9 else []
    return dict(zip(keys, fields[9].split(":"), strict=False)) if keys else {}


def reference_depth(
    info: Mapping[str, str], values: Mapping[str, str], *, block: bool
) -> int | None:
    """Block evidence needs an explicit minimum; point DP is not a block floor."""
    floors = [
        depth
        for value in (info.get("MIN_DP"), values.get("MIN_DP"))
        if (depth := _int(value)) is not None and depth >= 0
    ]
    if floors:
        return min(floors)
    return None if block else _int(values.get("DP"))


def record_state(
    fields: Sequence[str],
    model: str,
    thresholds: Thresholds,
    ploidy: int | None = None,
    *,
    check_variant_quality: bool = False,
) -> State:
    """The state of the bases one gVCF record covers (its first sample)."""
    values = _sample(fields)
    if fields[6] not in {"PASS", "."} or values.get("FT", ".") not in {"PASS", "."}:
        return State.FILTERED
    alleles = re.split(r"[/|]", values.get("GT", "."))
    alts = fields[4].split(",")
    carried = [a for a in alleles if a.isdigit() and a != "0" and int(a) <= len(alts)]
    scale = 0.5 if (ploidy if ploidy is not None else len(alleles)) == 1 else 1.0
    if any(alts[int(a) - 1] not in SYMBOLIC_REFERENCE_ALTS for a in carried):
        if not check_variant_quality:
            return State.VARIANT
        depth, gq = _int(values.get("DP")), _float(values.get("GQ"))
        if (
            depth is None
            or depth == 0
            or depth < thresholds.min_depth * scale
            or (model == GQ and (gq is None or gq < thresholds.min_gq))
            or any(a not in carried and a != "0" for a in alleles)
            or any(
                re.fullmatch(r"[ACGT]+", allele, re.IGNORECASE) is None
                for allele in (fields[3], *(alts[int(a) - 1] for a in carried))
            )
        ):
            return State.LOW_QUALITY_VARIANT
        return State.VARIANT
    if carried or any(a != "0" for a in alleles):
        return State.NO_CALL
    info = _info(fields[7])
    block = "END" in info and all(
        alt in SYMBOLIC_REFERENCE_ALTS or alt == "." for alt in alts
    )
    depth = reference_depth(info, values, block=block)
    if depth == 0:
        return State.UNCOVERED
    if depth is None or depth < thresholds.min_depth * scale:
        return State.LOW_QUALITY_REFERENCE
    if model == GQ:
        gq = _float(values.get("GQ"))
        if gq is None or gq < thresholds.min_gq:
            return State.LOW_QUALITY_REFERENCE
    return State.CALLABLE_REFERENCE


def _record_end(fields: Sequence[str]) -> int:
    end = _int(_info(fields[7]).get("END"))
    return end if end is not None else int(fields[1]) + len(fields[3]) - 1


def _merge(intervals: Iterable[Interval], order: Mapping[str, int]) -> list[Interval]:
    merged: list[Interval] = []
    for contig, start, end in sorted(
        intervals, key=lambda item: (order.get(item[0], len(order)), item[1])
    ):
        if merged and merged[-1][0] == contig and start <= merged[-1][2] + 1:
            merged[-1] = (contig, merged[-1][1], max(merged[-1][2], end))
        else:
            merged.append((contig, start, end))
    return merged


def _sweep(
    wanted: list[Interval],
    records: Iterator[Classified],
    order: Mapping[str, int],
    *,
    missing_state: State = State.UNCOVERED,
) -> Iterator[Span]:
    """Resolve overlapping records base by base, highest-ranked state first.
    Records arrive sorted, so only the few overlapping the cursor are held."""
    pending = next(records, None)
    active: list[tuple[str, int, State]] = []
    for contig, first, last in wanted:
        rank = order[contig]
        # Records of contigs already passed (none, from bcftools -R) are skipped.
        while pending is not None and order.get(pending[0], -1) < rank:
            pending = next(records, None)
        cursor = first
        while cursor <= last:
            while pending is not None and pending[0] == contig and pending[1] <= cursor:
                if pending[2] >= cursor:
                    active.append((contig, pending[2], pending[3]))
                pending = next(records, None)
            active = [a for a in active if a[0] == contig and a[1] >= cursor]
            upcoming = last + 1
            if pending is not None and pending[0] == contig:
                upcoming = min(upcoming, pending[1])
            if active:
                state = min((a[2] for a in active), key=_RANK.__getitem__)
                stop = min(min(a[1] for a in active), upcoming - 1)
            else:
                state, stop = missing_state, upcoming - 1
            yield Span(contig, cursor, stop, state)
            cursor = stop + 1
