"""Offline research-panel filtering and burden counting on variant-table rows."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any, NamedTuple

from wgsextract_cli.core import panel, variant_table
from wgsextract_cli.core.csq_alleles import split_origin
from wgsextract_cli.core.lookup.identifiers import contig_key
from wgsextract_cli.core.panel_spec import PREDICTOR_THRESHOLDS
from wgsextract_cli.core.table_writer import Value

_COLUMN = {name: i for i, name in enumerate(variant_table.COLUMN_NAMES)}
_SCORES = {
    "min_revel": "revel",
    "min_cadd_phred": "cadd_phred",
    "min_alphamissense": "alphamissense_score",
    "min_spliceai": "spliceai_max",
}
_FREQUENCIES = {"max_af": "gnomad_af", "max_popmax_af": "gnomad_popmax_af"}
_CONFIDENCE = {"low": 1, "moderate": 2, "high": 3}
_BASES = re.compile(r"[ACGTN]+", re.IGNORECASE)


class Decision(str, Enum):
    REJECTED = "rejected"
    QUALIFYING = "qualifying"
    UNSCORED = "unscored"


class Assessment(NamedTuple):
    decision: Decision
    missing: tuple[str, ...] = ()
    weight: float | None = None
    clinvar_veto: bool = False


@dataclass(frozen=True)
class Allele:
    row: variant_table.Row
    gene: str
    record: int | str
    original_alt: int
    # Index of each genotype copy carrying this ALT, even on multi-ALT calls.
    copies: tuple[int, ...]
    ploidy: int
    complete: bool
    clinvar_aggregate: str | None
    structural: bool

    def get(self, name: str) -> Value:
        return self.row[_COLUMN[name]]

    def document(self, missing: tuple[str, ...]) -> dict[str, object]:
        fields = (
            "chrom", "pos", "ref", "alt", "rsid", "transcript_id", "genotype",
            "zygosity", "phase_set", "impact", "consequence", "confidence",
            *_FREQUENCIES.values(), *_SCORES.values(),
        )  # fmt: skip
        return {
            **{name: _finite_value(self.get(name)) for name in fields},
            "copies": len(self.copies),
            "missing": list(missing),
        }


def gene_alleles(
    records: Iterable[str],
    header: variant_table.VcfHeader,
    notes: panel.Annotations,
    symbols: set[str],
    *,
    clinvar_aggregate: bool = False,
) -> Iterator[Allele]:
    """Yield observed alleles with a resolved symbol and a matching gene span."""
    names: dict[str, str | None] = {}
    for (
        row,
        record,
        alt_index,
        origin,
        aggregate,
        structural,
    ) in variant_table.indexed_variant_rows(
        records, header, clinvar_aggregate=clinvar_aggregate
    ):
        name = row[_COLUMN["gene"]]
        if not isinstance(name, str) or row[_COLUMN["imputed"]]:
            continue
        if name not in names:
            names[name] = panel.match_gene(name, notes, suggest=False).symbol
        symbol = names[name]
        if symbol is None or symbol not in symbols:
            continue
        pos, chrom = row[_COLUMN["pos"]], contig_key(str(row[_COLUMN["chrom"]]))
        if not isinstance(pos, int) or not any(
            contig_key(locus.chrom) == chrom and locus.start < pos <= locus.end
            for locus in notes.loci[symbol]
        ):
            continue
        gt = re.split(r"[/|]", str(row[_COLUMN["genotype"]]))
        copies = tuple(i for i, value in enumerate(gt) if value == str(alt_index))
        if copies:
            original = split_origin(origin) if origin is not None else None
            record_key: int | str = record
            original_alt = alt_index
            if original is not None and origin is not None and len(original[1]) > 1:
                record_key, original_alt = origin.rsplit("|", 1)[0], original[2]
            yield Allele(
                row,
                symbol,
                record_key,
                original_alt,
                copies,
                len(gt),
                "." not in gt,
                aggregate,
                structural,
            )


def _number(value: Value) -> float | None:
    return value if isinstance(value, (int, float)) and isfinite(value) else None


def _finite_value(value: Value) -> Value:
    return None if isinstance(value, float) and not isfinite(value) else value


def weighted(filters: Mapping[str, Any], dosage: Mapping[str, float]) -> bool:
    return bool(dosage) or any("weight" in clause for clause in filters["qualify"])


def _preferred_score(
    allele: Allele, order: list[str]
) -> tuple[str | None, float | None]:
    for predictor in order:
        key = PREDICTOR_THRESHOLDS[predictor]
        value = _number(allele.get(_SCORES[key]))
        if value is not None and 0 <= value <= (
            100 if predictor == "cadd_phred" else 1
        ):
            return key, value
    return None, None


def assess(
    allele: Allele, filters: Mapping[str, Any], *, small_variants_only: bool = False
) -> Assessment:
    """Evaluate AND in each clause, then OR across clauses. False beats
    unknown within an AND; true beats unknown across an OR."""
    if small_variants_only and (
        allele.structural or not _BASES.fullmatch(str(allele.get("alt")))
    ):
        return Assessment(Decision.REJECTED)
    flag = allele.get("filter")
    if (
        filters["pass_only"]
        and flag != "PASS"
        and not (flag is None and filters.get("missing_filter") == "keep")
    ):
        return Assessment(Decision.REJECTED)
    missing = set()
    for key, column in _FREQUENCIES.items():
        if key not in filters:
            continue
        value = _number(allele.get(column))
        if value is None or not 0 <= value <= 1:
            missing.add(column)
        elif value > filters[key]:
            return Assessment(Decision.REJECTED)
    if minimum := filters.get("min_confidence"):
        confidence = allele.get("confidence")
        if _CONFIDENCE.get(str(confidence), 0) < _CONFIDENCE[minimum] and not (
            confidence is None and filters.get("missing_confidence") == "keep"
        ):
            return Assessment(Decision.REJECTED)

    order = filters.get("score_order")
    preferred, score = _preferred_score(allele, order) if order else (None, None)
    unknown = set()
    best: float | None = None
    for clause in filters["qualify"]:
        if "impacts" in clause and allele.get("impact") not in clause["impacts"]:
            continue
        if "consequences" in clause and not (
            set(str(allele.get("consequence") or "").split("&"))
            & set(clause["consequences"])
        ):
            continue
        clause_missing = set()
        for key, column in _SCORES.items():
            if key not in clause:
                continue
            if preferred is not None and preferred != key:
                break
            value = score if order else _number(allele.get(column))
            if value is None:
                clause_missing.add(column)
            elif value < clause[key]:
                break
        else:
            if not clause_missing:
                weight = clause.get("weight", 1)
                best = weight if best is None else max(weight, best)
                if best == 1:
                    break
            else:
                unknown.update(clause_missing)
    if best is not None:
        if filters.get("exclude_clinvar_benign") and _clinvar_benign(allele):
            return Assessment(Decision.REJECTED, clinvar_veto=True)
        return Assessment(Decision.QUALIFYING, tuple(sorted(missing)), best)
    if unknown and filters["missing_scores"] == "unscored":
        return Assessment(Decision.UNSCORED, tuple(sorted(missing | unknown)))
    return Assessment(Decision.REJECTED)


def _clinvar_benign(allele: Allele) -> bool:
    aggregate = (allele.clinvar_aggregate or "").lower().replace(" ", "_")
    if aggregate not in {
        "benign",
        "likely_benign",
        "benign/likely_benign",
        "likely_benign/benign",
    } or allele.get("clinvar_conflict_evidence"):
        return False
    review = str(allele.get("clinvar_review_status") or "").lower()
    return "conflicting" not in review


def allele_phase(alleles: Iterable[Allele]) -> tuple[str | None, str | None]:
    """Phase of qualifying diploid heterozygotes and the two-copy evidence."""
    records: dict[int | str, set[int]] = {}
    phase_sets: dict[tuple[str, str], set[int]] = {}
    hets = 0
    incomplete = False
    unphased = False
    both = False
    for allele in alleles:
        if not allele.complete or allele.ploidy != 2:
            incomplete = True
        elif len(allele.copies) == 2:
            both = True
        else:
            hets += 1
            records.setdefault(allele.record, set()).add(allele.original_alt)
            phase_set = allele.get("phase_set")
            if phase_set:
                chrom = contig_key(str(allele.get("chrom")))
                phase_sets.setdefault((chrom, str(phase_set)), set()).update(
                    allele.copies
                )
            else:
                unphased = True
    if any(len(alts) == 2 for alts in records.values()) or any(
        len(copies) == 2 for copies in phase_sets.values()
    ):
        return "trans", "biallelic"
    phase = None
    if hets >= 2:
        phase = "cis" if len(phase_sets) == 1 and not unphased else "unknown"
    if both:
        return phase, "biallelic"
    if incomplete and not hets:
        return phase, "not_established"
    if phase == "unknown" or (hets and incomplete):
        return phase, "possible_biallelic"
    return phase, "one_copy" if hets else None
