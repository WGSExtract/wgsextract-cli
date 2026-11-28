"""Same-sample null panels matched on coding opportunity, not disease risk."""

from __future__ import annotations

import random
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from itertools import combinations, product
from math import comb, isfinite, sqrt
from statistics import fmean, pstdev
from typing import Any, cast

from wgsextract_cli.core import exon_callability as ec
from wgsextract_cli.core.lookup.identifiers import contig_key
from wgsextract_cli.core.panel_cnv import MATCH_ON
from wgsextract_cli.core.panel_weights import STATISTIC, UNITS
from wgsextract_cli.core.reference_evidence import ReferenceEvidence, Span, State
from wgsextract_cli.core.sex_inference import nonpar_bounds
from wgsextract_cli.core.variant_confidence import Thresholds

Interval = tuple[int, int]
_AUTOSOMES = frozenset(str(i) for i in range(1, 23))
_CALLABLE = {State.CALLABLE_REFERENCE, State.VARIANT}
_GRCH38_X_LENGTH = 156_040_895
_MAX_LENGTH_IMBALANCE = 0.20
EXPECTED_BINS = 4
_EXACT_PERMUTATIONS = 100_000
NOTE = (
    "Compares this panel's filtered coding variant load with coding-length-matched "
    "genes in the same genome. Not evidence for disease association."
)


@dataclass(frozen=True)
class Opportunity:
    chrom: str
    chrom_class: str
    transcript: str
    coding_bp: int
    segments: tuple[Interval, ...]
    matched_bp: int
    duplicated_bp: int

    def covers(self, chrom: str, start: int, end: int) -> bool:
        return contig_key(chrom) == self.chrom and any(
            low <= start <= end <= high for low, high in self.segments
        )


@dataclass(frozen=True)
class CnvMatch:
    strata: Mapping[str, tuple[int, int, str]]
    components: Mapping[str, tuple[Fraction, Fraction]]


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
    sex: str | None = None,
) -> tuple[dict[str, Opportunity], dict[str, str]]:
    """Choose one MANE transcript per gene and count the union of coding
    bases. Masked candidate genes are excluded by CDS, not genomic span."""
    genes: dict[str, tuple[str, str, tuple[Interval, ...], int]] = {}
    excluded: dict[str, str] = {}
    allowed = _AUTOSOMES | {"X"} if sex in {"F", "M", "XX", "XY"} else _AUTOSOMES
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
        chrom_class = _chromosome_class(contig_key(chrom), merged, sex)
        if chrom_class is None:
            excluded[gene] = "mixed_x_ploidy"
            continue
        genes[gene] = chrom, chrom_class, merged, dup_bp

    called: dict[str, list[Span]] = defaultdict(list)
    ends: dict[str, list[int]] = defaultdict(list)
    if reference is not None:
        regions = (
            (names[chrom], start, end)
            for chrom, _, coding, _ in genes.values()
            for start, end in coding
        )
        for span in reference.spans(thresholds, regions, dense=True):
            if span.state in _CALLABLE:
                called[span.contig].append(span)
                ends[span.contig].append(span.end)
    result: dict[str, Opportunity] = {}
    for gene, (chrom, chrom_class, cds, dup_bp) in genes.items():
        if reference is None:
            compatible = cds
        else:
            compatible = _intersect(cds, called[names[chrom]], ends[names[chrom]])
        if not compatible:
            excluded[gene] = "no_callable_cds"
            continue
        result[gene] = Opportunity(
            contig_key(chrom),
            chrom_class,
            model.genes[gene][0].transcript,
            _bases(cds),
            compatible,
            _bases(compatible),
            dup_bp,
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


def _chromosome_class(
    chrom: str, cds: Sequence[Interval], sex: str | None
) -> str | None:
    if chrom != "X":
        return "autosomal"
    if sex not in {"M", "XY"}:
        return "X_diploid"
    nonpar = nonpar_bounds("X", _GRCH38_X_LENGTH)
    assert nonpar is not None
    left, right = nonpar
    # PARs flank the shared non-PAR interval, excluding the 10 kb end gaps.
    par_intervals = ((10_001, left), (right + 1, _GRCH38_X_LENGTH - 10_000))
    bases = _bases(cds)
    par = sum(
        max(0, min(end, high) - max(start, low) + 1)
        for start, end in cds
        for low, high in par_intervals
    )
    if par == bases:
        return "X_diploid"
    return "X_haploid" if par == 0 else None


def _near_range(value: float, bounds: tuple[float, float]) -> bool:
    delta = value * _MAX_LENGTH_IMBALANCE
    return bounds[0] - delta <= value <= bounds[1] + delta


def _permutation_tail(
    strata: Sequence[tuple[list[Fraction], int]],
    observed: int | Fraction,
    null_sets: int,
    seed: int,
) -> dict[str, Any]:
    assignments = 1
    for scores, size in strata:
        assignments *= comb(len(scores), size)
        if assignments > _EXACT_PERMUTATIONS:
            break
    if assignments <= _EXACT_PERMUTATIONS:
        totals = [
            [sum(selected, Fraction()) for selected in combinations(scores, size)]
            for scores, size in strata
        ]
        extreme = sum(sum(draw) >= observed for draw in product(*totals))
        return {
            "upper_tail_p": extreme / assignments,
            "p_method": "exact_stratified_label_permutation",
            "p_permutations": assignments,
        }
    rng = random.Random(seed)
    extreme = sum(
        sum(sum(rng.sample(scores, size)) for scores, size in strata) >= observed
        for _ in range(null_sets)
    )
    p = (1 + extreme) / (1 + null_sets)
    return {
        "upper_tail_p": p,
        "p_method": "monte_carlo_stratified_label_permutation",
        "p_permutations": null_sets,
        "p_monte_carlo_se": sqrt(p * (1 - p) / (1 + null_sets)),
    }


def _control_moments(
    pools: Sequence[tuple[list[str], int]], scores: Mapping[str, Fraction]
) -> tuple[Fraction, Fraction, Fraction]:
    mean = variance = contributing_genes = zero = Fraction()
    for pool, size in pools:
        n = len(pool)
        values = [scores[gene] for gene in pool]
        center = sum(values, zero) / n
        mean += size * center
        contributing_genes += Fraction(size * sum(bool(value) for value in values), n)
        # Finite-population correction for each sample without replacement.
        variance += Fraction(size * (n - size), n - 1) * (
            sum((score**2 for score in values), zero) / n - center**2
        )
    return mean, variance, contributing_genes


def _sqrt_fraction(value: Fraction) -> float:
    # Keep tiny squared weights out of the float range until after the root.
    with localcontext(Context(prec=32)):
        return float((Decimal(value.numerator) / Decimal(value.denominator)).sqrt())


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
    expected_syn: Mapping[str, float] | None = None,
    match_expected: bool = True,
    weighted_scores: Mapping[str, Fraction] | None = None,
    clinvar_vetoes: Mapping[str, int] | None = None,
    cnv: CnvMatch | None = None,
) -> dict[str, Any]:
    """Sample an equal number of genes in each target bin without replacement.

    Equal lengths stay in one bin, even at a quantile boundary. Each draw is
    independent, and contains none of the panel's genes (from any group).
    """
    control: dict[str, list[tuple[int, str]]] = defaultdict(list)
    target_set = set(targets) if cnv is not None else set()
    strata_design: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for gene, opportunity in opportunities.items():
        entry = (opportunity.matched_bp, gene)
        if gene not in panel_genes:
            control[opportunity.chrom_class].append(entry)
        if cnv is not None and (gene not in panel_genes or gene in target_set):
            strata_design[opportunity.chrom_class].append(entry)
    cds_bins: dict[tuple[str, int], list[str]] = defaultdict(list)
    limits: dict[str, list[int]] = {}
    for genes in control.values():
        genes.sort()
    for chrom_class, genes in (strata_design if cnv is not None else control).items():
        genes.sort()
        cuts = sorted({genes[len(genes) * n // bins][0] for n in range(1, bins)})
        limits[chrom_class] = cuts
        for bp, gene in genes:
            cds_bins[chrom_class, bisect_right(cuts, bp)].append(gene)
    syn_limits: dict[tuple[str, int], list[float]] = {}
    if expected_syn is not None and match_expected:
        for key, genes_in_bin in cds_bins.items():
            values = sorted(expected_syn[gene] for gene in genes_in_bin)
            syn_limits[key] = sorted(
                {
                    values[len(values) * i // EXPECTED_BINS]
                    for i in range(1, EXPECTED_BINS)
                }
            )

    Stratum = tuple[str, int, int] | tuple[str, int, int, int, int, str]

    def stratum(key: tuple[str, int], gene: str) -> Stratum:
        sub_bin = (
            bisect_right(syn_limits.get(key, ()), expected_syn[gene])
            if expected_syn is not None and match_expected
            else 0
        )
        base = (key[0], key[1], sub_bin)
        return (*base, *cnv.strata[gene]) if cnv is not None else base

    targets_by_bin: dict[Stratum, list[str]] = defaultdict(list)
    control_bins: dict[Stratum, list[str]] = defaultdict(list)
    for key, genes_in_bin in cds_bins.items():
        for gene in genes_in_bin:
            if gene not in target_set:
                control_bins[stratum(key, gene)].append(gene)
    joint_ranges = {}
    if (expected_syn is not None and match_expected) or cnv is not None:
        for bin_key, pool_genes in control_bins.items():
            lengths = [opportunities[gene].matched_bp for gene in pool_genes]
            expectations = (
                [expected_syn[gene] for gene in pool_genes]
                if expected_syn is not None and match_expected
                else []
            )
            joint_ranges[bin_key] = (
                (min(lengths), max(lengths)),
                (min(expectations), max(expectations)) if expectations else None,
            )
    outside = []
    outside_syn = []
    outside_joint_cds = []
    for gene in targets:
        opportunity = opportunities[gene]
        chrom_class = opportunity.chrom_class
        bin_ = bisect_right(limits.get(chrom_class, ()), opportunity.matched_bp)
        key = (chrom_class, bin_)
        bin_key = stratum(key, gene)
        targets_by_bin[bin_key].append(gene)
        eligible = control.get(chrom_class, ())
        if eligible and not (
            eligible[0][0] <= opportunity.matched_bp <= eligible[-1][0]
        ):
            outside.append(gene)
        if bin_key in joint_ranges:
            length_range, syn_range = joint_ranges[bin_key]
            if not _near_range(opportunity.matched_bp, length_range):
                outside_joint_cds.append(gene)
            if (
                expected_syn is not None
                and syn_range
                and not _near_range(expected_syn[gene], syn_range)
            ):
                outside_syn.append(gene)
    counts = [allele_counts.get(gene, 0) for gene in targets]
    observed_copies = sum(counts)
    observed: int | Fraction = observed_copies
    zero = Fraction()
    observed_bp = sum(opportunities[gene].matched_bp for gene in targets)
    document: dict[str, Any] = {
        "basis": basis,
        "note": NOTE,
        "observed_allele_copies": observed_copies,
        "observed_qualifying_genes": sum(bool(count) for count in counts),
        "observed_matched_bp": observed_bp,
        "target_genes": len(targets),
        "control_genes": sum(map(len, control.values())),
        "requested_null_sets": null_sets,
        "seed": seed,
        "bins": bins,
        "strata": [
            {
                "chromosome_class": key[0],
                "bin": key[1],
                **(
                    {"expected_syn_bin": key[2]}
                    if expected_syn is not None and match_expected
                    else {}
                ),
                **(
                    dict(zip(MATCH_ON, key[3:], strict=True)) if cnv is not None else {}
                ),
                "target_genes": len(genes),
                "control_genes": len(control_bins[key]),
            }
            for key, genes in sorted(targets_by_bin.items())
        ],
    }
    if weighted_scores is not None:
        observed = sum((weighted_scores.get(gene, zero) for gene in targets), zero)
        document.update(
            statistic=STATISTIC,
            units=UNITS,
            observed_weighted_allele_copies=float(observed),
        )
    if cnv is not None:
        deletion = sum((cnv.components[gene][0] for gene in targets), zero)
        duplication = sum((cnv.components[gene][1] for gene in targets), zero)
        observed += deletion + duplication
        document.pop("observed_weighted_allele_copies", None)
        document.update(
            statistic="combined_burden",
            units="weighted small-variant allele copies and CNV events",
            note="Same-sample matched burden; research only. Assays are not independent.",
            observed_combined_burden=float(observed),
            observed_small_variant_burden=float(observed - deletion - duplication),
            observed_deletion_burden=float(deletion),
            observed_duplication_burden=float(duplication),
        )
    if clinvar_vetoes is not None:
        document["clinvar_benign_exclusions"] = {
            "target_alleles": sum(clinvar_vetoes.get(gene, 0) for gene in targets),
            "control_pool_alleles": sum(
                count
                for gene, count in clinvar_vetoes.items()
                if gene in opportunities and gene not in panel_genes
            ),
        }
    observed_syn = (
        None if expected_syn is None else sum(expected_syn[g] for g in targets)
    )
    if observed_syn is not None:
        document["observed_expected_syn"] = observed_syn
    if not targets or any(
        len(genes) + int(cnv is not None) > len(control_bins[key])
        for key, genes in targets_by_bin.items()
    ):
        return {
            **document,
            "state": "unavailable",
            "reason": "no_eligible_target_genes"
            if not targets
            else "insufficient_matched_controls",
        }
    if outside:
        return {
            **document,
            "state": "unavailable",
            "reason": "outside_control_cds_range",
            "unmatched_targets": sorted(outside),
        }
    if outside_syn:
        return {
            **document,
            "state": "unavailable",
            "reason": "outside_control_expected_syn_range",
            "unmatched_targets": sorted(outside_syn),
        }
    if outside_joint_cds:
        return {
            **document,
            "state": "unavailable",
            "reason": "outside_joint_cds_range",
            "unmatched_targets": sorted(outside_joint_cds),
        }
    ordered_targets = sorted(targets_by_bin.items())
    pools = [(control_bins[key], len(genes)) for key, genes in ordered_targets]
    mean_bp = sum(
        size * fmean(opportunities[gene].matched_bp for gene in pool)
        for pool, size in pools
    )
    if abs(mean_bp - observed_bp) > observed_bp * _MAX_LENGTH_IMBALANCE:
        return {
            **document,
            "state": "unavailable",
            "reason": "coding_length_imbalance",
            "expected_control_cds_bp": mean_bp,
        }
    if expected_syn is not None and observed_syn is not None and match_expected:
        mean_syn = sum(
            size * fmean(expected_syn[gene] for gene in pool) for pool, size in pools
        )
        if abs(mean_syn - observed_syn) > observed_syn * _MAX_LENGTH_IMBALANCE:
            return {
                **document,
                "state": "unavailable",
                "reason": "expected_syn_imbalance",
                "expected_control_syn": mean_syn,
            }
    rng = random.Random(seed)
    joint_scores = None
    if cnv is not None:
        assert weighted_scores is not None
        joint_scores = {
            gene: weighted_scores.get(gene, zero) + sum(components, zero)
            for gene, components in cnv.components.items()
        }
    draws: list[int | Fraction] = []
    null_bp = 0
    null_syn = 0.0
    null_copies = 0
    for _ in range(null_sets):
        score: int | Fraction = 0
        for pool, size in pools:
            for gene in rng.sample(pool, size):
                copies = allele_counts.get(gene, 0)
                null_copies += copies
                if joint_scores is not None:
                    score += joint_scores[gene]
                else:
                    score += (
                        copies
                        if weighted_scores is None
                        else weighted_scores.get(gene, 0)
                    )
                null_bp += opportunities[gene].matched_bp
                if expected_syn is not None:
                    null_syn += expected_syn[gene]
        draws.append(score)
    histogram = Counter(draws)
    if joint_scores is not None:
        center, variance, contributing_genes = _control_moments(pools, joint_scores)
        sparse_null = contributing_genes < 1
        mean = float(center)
        sd = _sqrt_fraction(variance)
        zero_variance = not variance
        deviation = Fraction(observed) - center
        z_score = (
            _sqrt_fraction(deviation**2 / variance) * (-1 if deviation < 0 else 1)
            if variance
            else None
        )
    else:
        sparse_null = null_copies < null_sets
        mean = fmean(draws)
        center = (
            cast(Fraction, sum(draws, zero)) / null_sets
            if weighted_scores is not None
            else zero
        )
        sd = float(
            pstdev(Fraction(draw) for draw in draws)
            if weighted_scores is not None
            else pstdev(cast(list[int], draws))
        )
        zero_variance = not sd
        z_score = (
            float(observed - center) / sd
            if weighted_scores is not None and sd
            else None
        )
    less = sum(n for copies, n in histogram.items() if copies < observed)
    binned: Counter[int] = Counter()
    if weighted_scores is not None:
        for value, n in histogram.items():
            binned[value // 1] += n
    tail: dict[str, Any] = {"upper_tail_p": (1 + null_sets - less) / (null_sets + 1)}
    if joint_scores is not None:
        if zero_variance:
            tail = {"upper_tail_p": None, "p_reason": "zero_control_variance"}
        else:
            tail = _permutation_tail(
                [
                    (
                        [joint_scores[gene] for gene in [*control_bins[key], *genes]],
                        len(genes),
                    )
                    for key, genes in ordered_targets
                ],
                observed,
                null_sets,
                seed,
            )
    cnv_means = {}
    if cnv is not None:
        deletion, duplication = (
            sum(
                (
                    Fraction(size, len(pool))
                    * sum((cnv.components[gene][component] for gene in pool), zero)
                    for pool, size in pools
                ),
                zero,
            )
            for component in (0, 1)
        )
        cnv_means = {
            "null_mean_small_variant_burden": float(center - deletion - duplication),
            "null_mean_deletion_burden": float(deletion),
            "null_mean_duplication_burden": float(duplication),
            "null_mean_contributing_genes": float(contributing_genes),
        }
    return {
        **document,
        "state": "calibrated",
        "null_sets": null_sets,
        "null_mean": mean,
        "null_mean_matched_bp": null_bp / null_sets,
        **(
            {"null_mean_expected_syn": null_syn / null_sets}
            if expected_syn is not None
            else {}
        ),
        **cnv_means,
        "null_sd": sd,
        "null_histogram": dict(
            sorted((binned if weighted_scores is not None else histogram).items())
        ),
        **(
            {
                "null_histogram_bin_width": 1,
                "null_mean_allele_copies": null_copies / null_sets,
                "z_score": z_score
                if z_score is not None and isfinite(z_score)
                else None,
            }
            if weighted_scores is not None
            else {}
        ),
        **tail,
        "percentile": 100 * (less + histogram[observed] / 2) / null_sets,
        "underpowered": len(targets) < 10
        or sparse_null
        or (weighted_scores is not None and zero_variance),
    }
