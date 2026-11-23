"""Sweep explicit calls and reference evidence without a genotype matrix."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from heapq import heappop, heappush, merge
from itertools import groupby

from wgsextract_cli.commands.cohort_vcf import Prepared, chromosome_records
from wgsextract_cli.core.reference_evidence import Span, State
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.core.variant_confidence import Thresholds
from wgsextract_cli.core.variant_table import Row

_BASES = frozenset("ACGT")


def _format(row: list[str]) -> dict[str, str]:
    return dict(zip(row[8].split(":"), row[9].split(":"), strict=False))


def _at_least(value: str | None, minimum: float) -> bool:
    try:
        number = float(value or "")
        return math.isfinite(number) and number >= minimum
    except ValueError:
        return False


@dataclass(frozen=True)
class Call:
    ref: str
    candidates: frozenset[str]
    genotype: tuple[str, ...] | None


@dataclass(frozen=True)
class Record:
    start: int
    end: int
    calls: dict[int, Call]


def _snv(ref: str, alt: str) -> tuple[int, str] | None:
    if len(ref) != len(alt):
        return None
    change = None
    for offset, (r, a) in enumerate(zip(ref, alt, strict=True)):
        if a != r:
            if change is not None or a not in _BASES or r not in _BASES:
                return None
            change = offset, a
    return change


def _record(row: list[str], pos: int, gq: bool, thresholds: Thresholds) -> Record:
    ref = row[3].upper()
    alts = tuple(_snv(ref, alt) for alt in row[4].upper().split(","))
    values = _format(row)
    codes = re.split(r"[/|]", values.get("GT", "."))
    called = tuple(
        int(code) if code.isdecimal() and int(code) <= len(alts) else None
        for code in codes
    )
    span = next(
        (field[4:] for field in row[7].split(";") if field.startswith("END=")), ""
    )
    end = max(pos + len(ref) - 1, int(span) if span.isdecimal() else pos)
    changes = tuple(alts[code - 1] if code else None for code in called)
    passed = (
        len(called) in (1, 2)
        and all(
            code == 0 or alt is not None
            for code, alt in zip(called, changes, strict=True)
        )
        and row[6] in {"PASS", "."}
        and values.get("FT", ".") in {"PASS", "."}
        and _at_least(values.get("DP"), thresholds.min_depth * (len(called) / 2))
        and (not gq or _at_least(values.get("GQ"), thresholds.min_gq))
    )
    # REF and other called SNVs define a base at this offset. A carried
    # insertion, deletion, symbolic ALT or missing allele doesn't.
    offsets = {change[0] for change in alts if change is not None}
    if ref in _BASES and end == pos:
        offsets.add(0)
    calls = {}
    for offset in offsets:
        base = ref[offset]
        candidates = frozenset(
            change[1]
            for change in changes
            if change is not None and change[0] == offset
        )
        genotype = (
            tuple(
                sorted(
                    a[1] if a is not None and a[0] == offset else base for a in changes
                )
            )
            if passed
            else None
        )
        calls[pos + offset] = Call(base, candidates, genotype)
    return Record(pos, end, calls)


def _events(
    records: Iterator[list[str]], sample: int, gq: bool, thresholds: Thresholds
) -> Iterator[tuple[int, int, Record]]:
    # Per-ALT trimming can move a padded SNV to the right of the record's
    # start. Keep only those future event positions until the stream reaches them.
    pending: list[tuple[int, int, Record]] = []
    for ordinal, row in enumerate(records):
        start = int(row[1])
        while pending and pending[0][0] <= start:
            pos, _, record = heappop(pending)
            yield pos, sample, record
        record = _record(row, start, gq, thresholds)
        yield start, sample, record
        for pos in record.calls:
            if pos > start:
                heappush(pending, (pos, ordinal, record))
    while pending:
        pos, _, record = heappop(pending)
        yield pos, sample, record


def _diploid_evidence(records: Iterator[list[str]]) -> Iterator[list[str]]:
    # Reference blocks are autosomal diploid here. Never turn a zero-copy,
    # partial or unknown-ploidy block into two reference alleles.
    for row in records:
        gt = _format(row).get("GT", ".")
        if gt not in {"0/0", "0|0"} and set(re.split(r"[/|]", gt)) == {"0"}:
            gt_index = row[8].split(":").index("GT")
            values = row[9].split(":")
            values[gt_index] = "."
            row[9] = ":".join(values)
        yield row


class EvidenceCursor:
    def __init__(self, spans: Iterator[Span]) -> None:
        self.spans = spans
        self.current: Span | None = None

    def at(self, position: int) -> State:
        if self.current is None or self.current.end < position:
            self.current = next(
                (span for span in self.spans if span.end >= position), None
            )
        if self.current is not None and self.current.start <= position:
            return self.current.state
        return State.NOT_ASSESSED


def _genotype(records: list[Record], pos: int) -> tuple[str, ...] | None:
    calls = [record.calls.get(pos) for record in records]
    genotypes = {call.genotype for call in calls if call is not None}
    if len(genotypes) > 1:
        raise UsageError("Conflicting normalized genotype calls for a participant.")
    return next(iter(genotypes), None) if None not in calls else None


def _site_rows(
    chrom: str,
    pos: int,
    keys: set[tuple[str, str]],
    active: list[list[Record]],
    evidence: list[EvidenceCursor],
) -> Iterator[Row]:
    ref = next(iter(keys))[0]  # The single-base REF has been checked against the FASTA.
    genotypes: Counter[tuple[str, ...]] = Counter()
    missing: Counter[State] = Counter()
    for sample, cursor in enumerate(evidence):
        state = cursor.at(pos)
        current = [record for record in active[sample] if record.end >= pos]
        active[sample] = current
        if state is State.EXCLUDED_BY_POLICY:
            missing[state] += 1
        elif current:
            if (gt := _genotype(current, pos)) is None:
                missing[State.NO_CALL] += 1
            else:
                genotypes[gt] += 1
        elif state is State.CALLABLE_REFERENCE:
            genotypes[ref, ref] += 1
        else:
            missing[state] += 1
    an = sum(len(gt) * n for gt, n in genotypes.items())
    callable_samples = genotypes.total()
    unassessed = missing[State.NOT_ASSESSED]
    for ref, alt in sorted(keys):
        ac = het = hom_alt = hemi = 0
        for gt, n in genotypes.items():
            copies = gt.count(alt)
            ac += copies * n
            if copies:
                if len(gt) == 1:
                    hemi += n
                elif copies == 2:
                    hom_alt += n
                else:
                    het += n
        yield (
            chrom,
            pos,
            ref,
            alt,
            ac,
            an,
            ac / an if an else None,
            het,
            hom_alt,
            hemi,
            callable_samples,
            genotypes[(ref,)] + genotypes[ref, ref],
            unassessed,
            missing.total() - unassessed,
        )


def chromosome_rows(
    prepared: Iterable[Prepared],
    chrom: str,
    length: int,
    ranges: list[tuple[int, int]],
    thresholds: Thresholds,
) -> Iterator[Row]:
    with ExitStack() as stack:
        sources, cursors = [], []
        active: list[list[Record]] = []
        for sample, item in enumerate(prepared):
            explicit = stack.enter_context(chromosome_records(item.variants, chrom))
            records = stack.enter_context(chromosome_records(item.evidence, chrom))
            sources.append(
                _events(
                    explicit,
                    sample,
                    item.explicit_gq,
                    thresholds,
                )
            )
            spans = item.reference_calls.spans(
                thresholds,
                [(chrom, 1, length)],
                records=_diploid_evidence(records),
                ploidy=2,
                stream_complete=item.reference_calls.provenance is not None,
            )
            cursors.append(EvidenceCursor(spans))
            active.append([])
        regions = iter(ranges)
        region = next(regions, None)
        for pos, group in groupby(
            merge(*sources, key=lambda entry: entry[:2]), key=lambda entry: entry[0]
        ):
            while region is not None and region[1] < pos:
                region = next(regions, None)
            if region is None:
                break
            keys: set[tuple[str, str]] = set()
            for _, sample, record in group:
                if record.start == pos:
                    active[sample] = [r for r in active[sample] if r.end >= pos]
                    active[sample].append(record)
                if (call := record.calls.get(pos)) is not None:
                    keys.update((call.ref, alt) for alt in call.candidates)
            if keys and region[0] < pos:
                yield from _site_rows(chrom, pos, keys, active, cursors)
