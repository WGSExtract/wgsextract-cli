"""Per-gene research burden, shared by the audit and its matched controls."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING

from wgsextract_cli.core.lookup.identifiers import contig_key

if TYPE_CHECKING:
    from wgsextract_cli.core.panel_audit import Allele

STATISTIC = "weighted_allele_copies"
UNITS = "weighted ALT copies"


@dataclass(frozen=True)
class Hit:
    allele: Allele
    clause_weight: float
    copies: int
    haploid: bool = False


@dataclass(frozen=True)
class Contribution:
    category: str
    multiplier: float
    weight: Fraction

    def document(self, hit: Hit) -> dict[str, float | str]:
        return {
            "clause_weight": hit.clause_weight,
            "dosage_category": self.category,
            "dosage_multiplier": self.multiplier,
            "weighted_allele_copies": float(self.weight),
        }


def _block(hit: Hit) -> tuple[str, str] | None:
    phase = hit.allele.get("phase_set")
    if phase is None:
        return None
    return contig_key(str(hit.allele.get("chrom"))), str(phase)


def _categories(hits: Sequence[Hit]) -> list[str]:
    records: dict[int | str, set[int]] = defaultdict(set)
    blocks: dict[tuple[str, str], set[int]] = defaultdict(set)
    heterozygotes = 0
    unresolved = False
    complex_calls = False
    for hit in hits:
        allele = hit.allele
        if hit.haploid or (allele.complete and allele.ploidy == 1):
            continue
        if not allele.complete or allele.ploidy != 2:
            unresolved = True
            complex_calls = True
        elif hit.copies == 1:
            heterozygotes += 1
            records[allele.record].add(allele.original_alt)
            if (block := _block(hit)) is not None:
                blocks[block].update(allele.copies)
            else:
                unresolved = True
    categories = []
    for hit in hits:
        allele = hit.allele
        if hit.haploid or (allele.complete and allele.ploidy == 1):
            category = "haploid"
        elif not allele.complete or allele.ploidy != 2:
            category = "phase_unknown"
        elif hit.copies == 2:
            category = "hom"
        elif len(records[allele.record]) > 1:
            category = "trans"
        elif heterozygotes < 2 and not complex_calls:
            category = "het"
        elif not unresolved and len(blocks) == 1:
            block = _block(hit)
            assert block is not None
            category = "trans" if len(blocks[block]) == 2 else "cis"
        else:
            category = "phase_unknown"
        categories.append(category)
    return categories


def score_gene(hits: Sequence[Hit], dosage: Mapping[str, float]) -> list[Contribution]:
    result = []
    for hit, category in zip(hits, _categories(hits), strict=True):
        multiplier = dosage.get(category, 1)
        # Compare null draws in exact decimal units, not rounded histogram bins
        # or binary floating point sums whose ties depend on addition order.
        weight = (
            hit.copies * Fraction(str(hit.clause_weight)) * Fraction(str(multiplier))
        )
        result.append(Contribution(category, multiplier, weight))
    return result


def components(
    hits: Sequence[Hit], contributions: Sequence[Contribution]
) -> dict[str, dict[str, int | float]]:
    copies: Counter[str] = Counter()
    weights: dict[str, Fraction] = defaultdict(Fraction)
    for hit, contribution in zip(hits, contributions, strict=True):
        copies[contribution.category] += hit.copies
        weights[contribution.category] += contribution.weight
    return {
        category: {
            "allele_copies": copies[category],
            STATISTIC: float(weights[category]),
        }
        for category in sorted(copies)
    }
