"""Merge normalized allele streams and resolve each callset's evidence states."""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from collections.abc import Generator, Iterable, Iterator
from dataclasses import dataclass
from heapq import merge
from itertools import groupby
from pathlib import Path
from typing import TextIO

from wgsextract_cli.core.reference_evidence import ReferenceEvidence, site_state
from wgsextract_cli.core.reference_evidence_records import Span, State
from wgsextract_cli.core.region_selection import bed_intervals, literal_region
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.core.variant_confidence import Thresholds
from wgsextract_cli.core.variant_files import match_chromosome_name

from .compare_inputs import PARTIAL, Dictionary, genotype

Allele = tuple[str, int, str, str]
_SMALL_BASES = frozenset("ACGTN")


class Scope:
    def __init__(
        self, dictionary: Dictionary, bed: Path | None, region: str | None
    ) -> None:
        self.limited = bed is not None or region is not None
        bounds = {
            name: dictionary.lengths.get(name, 2**63 - 1)
            for name in dictionary.names.values()
        }
        try:
            if bed is not None:
                rows = bed_intervals(str(bed))
            elif region is not None:
                name = region if region in bounds else region.rsplit(":", 1)[0]
                target = match_chromosome_name(name, bounds)
                if target is None:
                    raise ValueError(f"Unknown contig: {name}")
                rows = [literal_region(target + region[len(name) :], bounds)]
            else:
                rows = []
        except (OSError, ValueError) as exc:
            raise UsageError(f"Invalid comparison scope: {exc}") from exc
        by_contig: dict[str, list[tuple[int, int]]] = {}
        for name, start, end in rows:
            chrom = match_chromosome_name(name, bounds)
            if chrom is None:
                raise UsageError(f"Comparison scope names unknown contig {name!r}.")
            end = min(end, bounds[chrom])
            if start < end:
                by_contig.setdefault(chrom, []).append((start, end))
        self.intervals: dict[str, tuple[list[int], list[int]]] = {}
        for chrom, intervals in by_contig.items():
            starts: list[int] = []
            ends: list[int] = []
            for start, end in sorted(intervals):
                if ends and start <= ends[-1]:
                    ends[-1] = max(ends[-1], end)
                else:
                    starts.append(start)
                    ends.append(end)
            self.intervals[chrom] = starts, ends
        if self.limited and not self.intervals:
            raise UsageError("Comparison scope covers no reference bases.")

    def contains(self, allele: Allele) -> bool:
        if not self.limited:
            return True
        chrom, pos, ref, _ = allele
        starts, ends = self.intervals.get(chrom, ([], []))
        index = bisect_right(ends, pos - 1)
        return index < len(starts) and starts[index] < pos - 1 + len(ref)


@dataclass(frozen=True)
class Call:
    state: str
    dosage: int | None = None
    ploidy: int | None = None
    record: str | None = None

    @property
    def carried(self) -> bool:
        return self.state in {"carried", "partial_alt"}

    def signature(self) -> tuple[str, int | None, int | None]:
        return self.state, self.dosage, self.ploidy


def _observation(fields: list[str], line: str) -> Call:
    sample = dict(zip(fields[8].split(":"), fields[9].split(":"), strict=False))
    if fields[6] not in {".", "PASS"} or sample.get("FT", ".") not in {".", "PASS"}:
        return Call("filtered")
    alleles = genotype(fields)
    dosage = alleles.count("1")
    complete = PARTIAL not in fields[7].split(";")
    if dosage:
        return Call(
            "carried" if complete else "partial_alt", dosage, len(alleles), line
        )
    return Call("not_called" if complete else "no_call")


def records(
    stream: TextIO,
    index: int,
    order: dict[str, int],
) -> Iterator[tuple[tuple[int, int], int, Allele, Call]]:
    for line in stream:
        if line.startswith("#"):
            continue
        line = line.rstrip("\n")
        fields = line.split("\t")
        chrom, pos, ref, alt = (
            fields[0],
            int(fields[1]),
            fields[3].upper(),
            fields[4].upper(),
        )
        if not alt or alt == ref or (alt != "." and not _SMALL_BASES.issuperset(alt)):
            continue
        allele = chrom, pos, ref, alt
        call = _observation(fields, line)
        if alt == "." and call.carried:
            call = Call("no_call")
        yield (order[chrom], pos), index, allele, call


class EvidenceCursor:
    """Join forward-only candidate sites to evidence without loading a chromosome."""

    def __init__(
        self, source: ReferenceEvidence | None, thresholds: Thresholds
    ) -> None:
        self.source = source
        self.thresholds = thresholds
        self.chrom: str | None = None
        self.length = 0
        self.spans: Generator[Span, None, None] | None = None
        self.active: deque[Span] = deque()

    def close(self) -> None:
        if self.spans is not None:
            self.spans.close()
        self.spans = None
        self.active.clear()
        self.length = 0

    def _open_contig(self, chrom: str, source: ReferenceEvidence) -> None:
        self.close()
        self.chrom = chrom
        target = match_chromosome_name(chrom, source.lengths)
        if target is not None:
            self.length = source.lengths[target]
            self.spans = self._read(source, target)
            self._advance()

    def _read(
        self, source: ReferenceEvidence, target: str
    ) -> Generator[Span, None, None]:
        yield from source.spans(self.thresholds, [(target, 1, self.length)])

    def _advance(self) -> bool:
        if self.spans is not None and (span := next(self.spans, None)) is not None:
            self.active.append(span)
            return True
        return False

    def state(self, allele: Allele) -> State:
        if self.source is None:
            return State.NOT_ASSESSED
        chrom, start, ref, alt = allele
        if chrom != self.chrom:
            self._open_contig(chrom, self.source)
        end = start + len(ref) - 1
        if len(alt) > len(ref) and end < self.length:
            end += 1
        while self.active and self.active[0].end < start:
            self.active.popleft()
            if not self.active:
                self._advance()
        while self.active and self.active[-1].end < end and self._advance():
            pass
        covered = 0
        states = []
        for span in self.active:
            if span.start > end:
                break
            states.append(span.state)
            covered += min(span.end, end) - max(span.start, start) + 1
        if covered < end - start + 1:
            states.append(State.NOT_ASSESSED)
        return site_state(states)


def _dedup(observations: list[Call]) -> Call:
    first = observations[0]
    if any(call.signature() != first.signature() for call in observations[1:]):
        return Call("conflicting_records")
    return min(observations, key=lambda call: call.record or "")


def _at_locus(
    call: Call | None,
    has_other_alt: bool,
    uncertain: Call | None,
) -> Call | None:
    if call is not None and call.state != "not_called":
        return call
    if has_other_alt:
        return Call("other_alt")
    return uncertain or call


def sites(
    streams: Iterable[TextIO],
    order: dict[str, int],
    scope: Scope,
) -> Iterator[tuple[Allele, list[Call | None]]]:
    incoming = [records(stream, i, order) for i, stream in enumerate(streams)]
    combined = merge(*incoming, key=lambda item: item[0])
    for _, rows in groupby(combined, key=lambda item: item[0]):
        observed: dict[Allele, dict[int, list[Call]]] = {}
        for _, index, allele, call in rows:
            observed.setdefault(allele, {}).setdefault(index, []).append(call)
        candidates = [
            allele
            for allele, observations in observed.items()
            if scope.contains(allele)
            and any(call.carried for source in observations.values() for call in source)
        ]
        if not candidates:
            continue
        resolved = {
            allele: {i: _dedup(calls) for i, calls in calls_by_input.items()}
            for allele, calls_by_input in observed.items()
        }
        carried = {
            i
            for calls in resolved.values()
            for i, call in calls.items()
            if call.carried
        }
        uncertain: dict[int, Call] = {}
        for state in ("no_call", "filtered", "conflicting_records"):
            uncertain.update(
                (i, call)
                for calls in resolved.values()
                for i, call in calls.items()
                if call.state == state
            )
        for allele in sorted(candidates):
            calls = resolved[allele]
            yield (
                allele,
                [
                    _at_locus(calls.get(i), i in carried, uncertain.get(i))
                    for i in range(len(incoming))
                ],
            )


def resolve(
    allele: Allele,
    observation: Call | None,
    cursor: EvidenceCursor | None,
) -> tuple[Call, bool]:
    if cursor is None:
        return observation or Call("absent"), False
    evidence = cursor.state(allele)
    if observation is not None and observation.state != "not_called":
        return observation, observation.carried and evidence is State.CALLABLE_REFERENCE
    if evidence is State.NOT_ASSESSED and observation is not None:
        return observation, False
    return Call(
        "variant_evidence" if evidence is State.VARIANT else evidence.value
    ), False
