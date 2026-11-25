"""Same-sample null panels matched on coding opportunity, not disease risk."""

from __future__ import annotations

import random
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Any

from wgsextract_cli.core import exon_callability as ec
from wgsextract_cli.core.lookup.identifiers import contig_key
from wgsextract_cli.core.reference_evidence import ReferenceEvidence, Span, State
from wgsextract_cli.core.variant_confidence import Thresholds

Interval = tuple[int, int]
_AUTOSOMES = frozenset(str(i) for i in range(1, 23))
_CALLABLE = {State.CALLABLE_REFERENCE, State.VARIANT}
NOTE = (
    "Compares this panel's filtered coding variant load with coding-length-matched "
    "genes in the same genome. Not evidence for disease association."
)


@dataclass(frozen=True)
class Opportunity:
    chrom: str
    coding_bp: int
    segments: tuple[Interval, ...]
    matched_bp: int
    duplicated_bp: int

    def covers(self, chrom: str, start: int, end: int) -> bool:
        return contig_key(chrom) == self.chrom and any(
            low <= start <= end <= high for low, high in self.segments
        )


def _merge(intervals: Iterable[Interval]) -> tuple[Interval, ...]:
    out: list[Interval] = []
    for start, end in sorted(intervals):
        if out and start <= out[-1][1] + 1:
            low, high = out[-1]
            out[-1] = low, max(high, end)
        else:
            out.append((start, end))
    return tuple(out)


def _bases(intervals: Iterable[Interval]) -> int:
    return sum(high - low + 1 for low, high in intervals)


def opportunities(
    model: ec.ExonModel,
    names: Mapping[str, str],
    reference: ReferenceEvidence | None,
    thresholds: Thresholds,
    *,
    include_x: bool = False,
) -> tuple[dict[str, Opportunity], dict[str, str]]:
    """Choose one MANE transcript per gene and count the union of coding
    bases. Masked candidate genes are excluded by CDS, not genomic span."""
    genes: dict[str, tuple[str, tuple[Interval, ...], int]] = {}
    excluded: dict[str, str] = {}
    allowed = _AUTOSOMES | {"X"} if include_x else _AUTOSOMES
    for gene, exons in model.genes.items():
        chroms = {e.chrom for e in exons}
        if len(chroms) != 1 or any(e.tag != "MANE Select" for e in exons):
            excluded[gene] = "no_mane_select_transcript"
            continue
        chrom = next(iter(chroms))
        if chrom not in names or contig_key(chrom) not in allowed:
            excluded[gene] = "chromosome_out_of_scope"
            continue
        coding: list[Interval] = []
        duplicated: list[Interval] = []
        for exon in exons:
            if (bounds := exon.region(0)) is None:
                continue
            low, high = bounds
            coding.append(bounds)
            duplicated.extend(
                (max(start, low), min(end, high))
                for kind, start, end in exon.limitations
                if kind == "segmental_duplication" and start <= high and end >= low
            )
        merged = _merge(coding)
        if not merged:
            excluded[gene] = "no_coding_bases"
            continue
        dup_bp = _bases(_merge(duplicated))
        if 2 * dup_bp >= _bases(merged):
            excluded[gene] = "segmental_duplication"
            continue
        genes[gene] = chrom, merged, dup_bp

    called: dict[str, list[Span]] = defaultdict(list)
    ends: dict[str, list[int]] = defaultdict(list)
    if reference is not None:
        regions = (
            (names[chrom], start, end)
            for chrom, coding, _ in genes.values()
            for start, end in coding
        )
        for span in reference.spans(thresholds, regions):
            if span.state in _CALLABLE:
                called[span.contig].append(span)
                ends[span.contig].append(span.end)
    result: dict[str, Opportunity] = {}
    for gene, (chrom, cds, dup_bp) in genes.items():
        if reference is None:
            compatible = cds
        else:
            compatible = _intersect(cds, called[names[chrom]], ends[names[chrom]])
        if not compatible:
            excluded[gene] = "no_callable_cds"
            continue
        result[gene] = Opportunity(
            contig_key(chrom), _bases(cds), compatible, _bases(compatible), dup_bp
        )
    return result, excluded


def _intersect(
    intervals: Iterable[Interval], spans: Sequence[Span], ends: Sequence[int]
) -> tuple[Interval, ...]:
    parts: list[Interval] = []
    for start, end in intervals:
        index = bisect_left(ends, start)
        while index < len(spans) and spans[index].start <= end:
            span = spans[index]
            parts.append((max(start, span.start), min(end, span.end)))
            index += 1
    return _merge(parts)


def _class(chrom: str) -> str:
    return "autosomal" if chrom in _AUTOSOMES else chrom


def matched_null(
    targets: Sequence[str],
    panel_genes: set[str],
    opportunities: Mapping[str, Opportunity],
    allele_counts: Mapping[str, int],
    *,
    bins: int,
    null_sets: int,
    seed: int,
    basis: str,
) -> dict[str, Any]:
    """Sample an equal number of genes in each target bin without replacement.

    Equal lengths stay in one bin, even at a quantile boundary. Each draw is
    independent, and contains none of the panel's genes (from any group).
    """
    control: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for gene, opportunity in opportunities.items():
        if gene not in panel_genes:
            control[_class(opportunity.chrom)].append((opportunity.matched_bp, gene))
    target_bins: Counter[tuple[str, int]] = Counter()
    control_bins: dict[tuple[str, int], list[str]] = defaultdict(list)
    limits: dict[str, list[int]] = {}
    for chrom_class, genes in control.items():
        genes.sort()
        cuts = sorted({genes[len(genes) * n // bins][0] for n in range(1, bins)})
        limits[chrom_class] = cuts
        for bp, gene in genes:
            control_bins[chrom_class, bisect_right(cuts, bp)].append(gene)
    for gene in targets:
        opportunity = opportunities[gene]
        chrom_class = _class(opportunity.chrom)
        bin_ = bisect_right(limits.get(chrom_class, ()), opportunity.matched_bp)
        target_bins[chrom_class, bin_] += 1
    counts = [allele_counts.get(gene, 0) for gene in targets]
    observed = sum(counts)
    document: dict[str, Any] = {
        "basis": basis,
        "note": NOTE,
        "observed_allele_copies": observed,
        "observed_qualifying_genes": sum(bool(count) for count in counts),
        "target_genes": len(targets),
        "control_genes": sum(map(len, control.values())),
        "requested_null_sets": null_sets,
        "seed": seed,
        "bins": bins,
        "strata": [
            {
                "chromosome_class": key[0],
                "bin": key[1],
                "target_genes": count,
                "control_genes": len(control_bins[key]),
            }
            for key, count in sorted(target_bins.items())
        ],
    }
    if not targets or any(
        count > len(control_bins[key]) for key, count in target_bins.items()
    ):
        return {
            **document,
            "state": "unavailable",
            "reason": "no_eligible_target_genes"
            if not targets
            else "insufficient_matched_controls",
        }
    pools = [
        ([allele_counts.get(gene, 0) for gene in control_bins[key]], size)
        for key, size in sorted(target_bins.items())
    ]
    rng = random.Random(seed)
    draws = [
        sum(sum(rng.sample(pool, size)) for pool, size in pools)
        for _ in range(null_sets)
    ]
    histogram = Counter(draws)
    mean = fmean(draws)
    less = sum(n for copies, n in histogram.items() if copies < observed)
    return {
        **document,
        "state": "calibrated",
        "null_sets": null_sets,
        "null_mean": mean,
        "null_sd": pstdev(draws),
        "null_histogram": dict(sorted(histogram.items())),
        "upper_tail_p": (1 + null_sets - less) / (null_sets + 1),
        "percentile": 100 * (less + histogram[observed] / 2) / null_sets,
        "underpowered": len(targets) < 10 or mean < 1,
    }
