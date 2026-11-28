"""Run optional callable-CDS calibration for ``panel audit``."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from wgsextract_cli.api.panel import PanelAuditParams
from wgsextract_cli.core import exon_callability as ec
from wgsextract_cli.core import panel_audit as audit
from wgsextract_cli.core import panel_calibration as calibration
from wgsextract_cli.core import panel_cnv
from wgsextract_cli.core import panel_weights as weights
from wgsextract_cli.core.lookup.genotypes import VcfSource
from wgsextract_cli.core.lookup.identifiers import contig_key
from wgsextract_cli.core.panel_reference import (
    EXPECTED_SHA256,
    expected_syn,
    mane_model,
)
from wgsextract_cli.core.reference_evidence import ReferenceEvidence
from wgsextract_cli.core.region_selection import selected_intervals
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.core.variant_confidence import Thresholds


def _require_complete_calls(source: VcfSource, sex: str | None) -> None:
    region = source.provenance.region if source.provenance else None
    if not region:
        return
    eligible = {str(i) for i in range(1, 23)}
    if sex in {"F", "M", "XX", "XY"}:
        eligible.add("X")
    chromosomes = {c: n for c, n in source.lengths.items() if contig_key(c) in eligible}
    try:
        assessed = selected_intervals(region, source.lengths, commas=True, files=False)
    except ValueError:
        assessed = {}
    if not chromosomes or any(
        assessed.get(chrom) != [(0, length)] for chrom, length in chromosomes.items()
    ):
        raise UsageError(
            "Null calibration needs a whole-genome variant call set, but the VCF "
            f"records calling region {region!r}. Use --no-calibration."
        )


@dataclass
class Calibrator:
    opportunities: dict[str, calibration.Opportunity]
    excluded: dict[str, str]
    filters: dict[str, Mapping[str, Any]]
    filter_keys: dict[str, str]
    cnv_filter_keys: dict[str, str]
    counts: dict[str, Counter[str]]
    dosage: dict[str, Mapping[str, float]]
    hits: dict[str, dict[str, list[weights.Hit]]]
    vetoes: dict[str, Counter[str]]
    basis: str
    thresholds: dict[str, object] | None
    expected_counts: dict[str, float] | None

    def record(self, allele: audit.Allele) -> None:
        matched = self.opportunities.get(allele.gene)
        chrom, pos, ref = allele.get("chrom"), allele.get("pos"), allele.get("ref")
        if (
            matched is None
            or not isinstance(chrom, str)
            or not isinstance(pos, int)
            or not isinstance(ref, str)
            or not matched.covers(chrom, pos, pos + len(ref) - 1)
        ):
            return
        copies = len(allele.copies)
        if matched.chrom_class == "X_haploid":
            # Only a complete, unambiguous call can represent one haploid copy.
            if not allele.complete or copies != allele.ploidy:
                return
            copies = 1
        for key, filters in self.filters.items():
            assessment = audit.assess(
                allele, filters, small_variants_only=key in self.hits
            )
            if assessment.clinvar_veto:
                self.vetoes[key][allele.gene] += 1
            if assessment.decision is audit.Decision.QUALIFYING:
                self.counts[key][allele.gene] += copies
                if key in self.hits:
                    assert assessment.weight is not None
                    self.hits[key][allele.gene].append(
                        weights.Hit(
                            allele,
                            assessment.weight,
                            copies,
                            haploid=matched.chrom_class == "X_haploid",
                        )
                    )

    def finish(
        self,
        groups: Sequence[dict[str, Any]],
        settings: Mapping[str, Any],
        *,
        cnv: panel_cnv.Evidence | None,
    ) -> dict[str, object]:
        all_symbols = {
            gene["symbol"].upper()
            for group in groups
            for gene in group["genes"]
            if gene.get("symbol")
        }
        common = (
            {gene: self.opportunities[gene] for gene in self.expected_counts}
            if self.expected_counts is not None
            else {}
        )
        kwargs = {
            "bins": settings["bins"],
            "null_sets": settings["null_sets"],
            "seed": settings["seed"],
            "basis": self.basis,
        }
        scores = {
            key: {
                gene: sum(
                    (hit.weight for hit in weights.score_gene(hits, self.dosage[key])),
                    Fraction(),
                )
                for gene, hits in by_gene.items()
            }
            for key, by_gene in self.hits.items()
        }
        for group in groups:
            target = sorted(
                g["symbol"].upper()
                for g in group["genes"]
                if g.get("symbol") and g["symbol"].upper() in self.opportunities
            )
            excluded = {}
            for gene in group["genes"]:
                symbol = str(gene.get("symbol", "")).upper()
                if symbol:
                    if (opportunity := self.opportunities.get(symbol)) is not None:
                        gene["matched_cds_bp"] = opportunity.matched_bp
                        continue
                    excluded[gene["given"]] = self.excluded.get(symbol, "no_exon_model")
                else:
                    excluded[gene["given"]] = str(gene["status"])
            key = self.filter_keys[group["id"]]
            counts = self.counts[key]
            stats = calibration.matched_null(
                target,
                all_symbols,
                self.opportunities,
                counts,
                weighted_scores=scores.get(key),
                clinvar_vetoes=self.vetoes.get(key),
                **kwargs,
            )
            group["calibration"] = {**stats, "excluded_targets": excluded}
            if cnv is not None and panel_cnv.enabled(group):
                self._combined(group, all_symbols, cnv, scores, kwargs)
            if stats.get("underpowered") or stats["state"] != "calibrated":
                warn(
                    "panel_calibration_underpowered",
                    f"Group {group['id']} has too little matched coding evidence "
                    "for a well-powered null comparison.",
                    group=group["id"],
                    target_genes=len(target),
                    reason=stats.get("reason"),
                )
            if self.expected_counts is not None:
                covered = [gene for gene in target if gene in common]
                baseline = calibration.matched_null(
                    covered,
                    all_symbols,
                    common,
                    counts,
                    expected_syn=self.expected_counts,
                    match_expected=False,
                    weighted_scores=scores.get(key),
                    clinvar_vetoes=self.vetoes.get(key),
                    **kwargs,
                )
                joint = calibration.matched_null(
                    covered,
                    all_symbols,
                    common,
                    counts,
                    expected_syn=self.expected_counts,
                    weighted_scores=scores.get(key),
                    clinvar_vetoes=self.vetoes.get(key),
                    **kwargs,
                )
                base_low = baseline.get(
                    "underpowered", baseline["state"] != "calibrated"
                )
                joint_low = joint.get("underpowered", joint["state"] != "calibrated")
                group["calibration"]["expected_count_comparison"] = {
                    "missing_target_expected_count": sorted(
                        set(target) - common.keys()
                    ),
                    "cds_only_on_common_genes": baseline,
                    "cds_and_expected_syn": joint,
                    "underpowered": {
                        "cds_only": base_low,
                        "cds_and_expected_syn": joint_low,
                    },
                }
                if joint_low:
                    warn(
                        "panel_expected_count_underpowered",
                        f"Group {group['id']} has too little matched evidence for "
                        "the joint CDS and gnomAD expected-count comparison.",
                        group=group["id"],
                        target_genes=len(covered),
                        cds_only_underpowered=base_low,
                        reason=joint.get("reason"),
                    )
        return {
            "basis": self.basis,
            "thresholds": self.thresholds,
            "candidate_genes": len(self.opportunities),
            "excluded_gene_counts": dict(
                sorted(Counter(self.excluded.values()).items())
            ),
            "segmental_duplication_exclusions": sorted(
                gene
                for gene, reason in self.excluded.items()
                if reason == "segmental_duplication"
            ),
            **(
                {
                    "expected_syn": {
                        "source": "gnomAD v4.1 constraint, MANE Select, syn.exp",
                        "license": "CC0 1.0",
                        "sha256": EXPECTED_SHA256,
                        "bins_per_cds_stratum": calibration.EXPECTED_BINS,
                        "genes_with_counts": len(common),
                        "genes_without_counts": len(self.opportunities) - len(common),
                    }
                }
                if self.expected_counts is not None
                else {}
            ),
        }

    def _combined(
        self,
        group: dict[str, Any],
        panel_genes: set[str],
        cnv: panel_cnv.Evidence,
        scores: Mapping[str, Mapping[str, Fraction]],
        settings: Mapping[str, Any],
    ) -> None:
        report = group["cnv_calibration"]
        callability = report["callability"]
        if self.thresholds is None:
            report.update(state="unavailable", reason="missing_gvcf_callability")
            self._warn_combined(group)
            return
        strata = {}
        excluded_joint = {}
        excluded_controls: Counter[str] = Counter()
        for symbol, call in cnv.calls.items():
            opportunity = self.opportunities.get(symbol)
            if call.stratum is not None and call.region is not None and opportunity:
                chrom, start, end = call.region
                if opportunity.chrom == chrom and all(
                    start < low <= high <= end for low, high in opportunity.segments
                ):
                    strata[symbol] = call.stratum
                    continue
                excluded_joint[symbol] = "cds_outside_target"
            else:
                excluded_joint[symbol] = call.exclusion or self.excluded.get(
                    symbol, "no_exon_model"
                )
            if symbol not in panel_genes:
                excluded_controls[excluded_joint[symbol]] += 1
        expected = self.expected_counts
        eligible = set(strata)
        if expected is not None:
            missing_expected = eligible - expected.keys()
            eligible.difference_update(missing_expected)
            if count := len(missing_expected - panel_genes):
                excluded_controls["missing_expected_count"] += count
        callability["joint_control_genes"] = len(eligible - panel_genes)
        callability["excluded_control_counts"] = dict(sorted(excluded_controls.items()))
        target = []
        joint_excluded = dict(callability["excluded_targets"])
        for gene in group["genes"]:
            symbol = str(gene.get("symbol") or "").upper()
            if symbol in strata:
                target.append(symbol)
            elif gene["given"] not in joint_excluded:
                joint_excluded[gene["given"]] = excluded_joint.get(
                    symbol, "no_exon_model"
                )
        callability["joint_excluded_targets"] = joint_excluded
        callability["joint_callable_target_genes"] = len(target)
        stats: dict[str, Any]
        if joint_excluded:
            stats = {"state": "unavailable", "reason": "incomplete_target_evidence"}
        else:
            key = self.cnv_filter_keys[group["id"]]
            if missing := sorted(set(target) - eligible):
                stats = {
                    "state": "unavailable",
                    "reason": "missing_target_expected_count",
                    "unmatched_targets": missing,
                }
            else:
                stats = calibration.matched_null(
                    sorted(target),
                    panel_genes,
                    {symbol: self.opportunities[symbol] for symbol in eligible},
                    self.counts[key],
                    weighted_scores=scores[key],
                    clinvar_vetoes=self.vetoes.get(key),
                    expected_syn=expected,
                    cnv=calibration.CnvMatch(
                        strata,
                        {
                            symbol: cnv.calls[symbol].components(group)
                            for symbol in eligible
                        },
                    ),
                    **settings,
                )
        if stats["state"] != "calibrated":
            stats.pop("observed_combined_burden", None)
        report.update(stats)
        self._warn_combined(group)

    @staticmethod
    def _warn_combined(group: dict[str, Any]) -> None:
        stats = group["cnv_calibration"]
        if stats["state"] != "calibrated" or stats.get("underpowered"):
            warn(
                "panel_cnv_calibration_underpowered",
                f"Group {group['id']} lacks a well-powered joint CNV comparison.",
                group=group["id"],
                reason=stats.get("reason"),
            )


def prepare(
    params: PanelAuditParams,
    source: VcfSource,
    build: str,
    groups: Sequence[dict[str, Any]],
    evidence: ReferenceEvidence | None,
    thresholds: Thresholds,
    *,
    use_expected_counts: bool,
) -> tuple[Calibrator, ec.ExonModel]:
    if build != "GRCh38":
        raise UsageError(
            "MANE Select null calibration requires GRCh38; use --no-calibration."
        )
    if not groups:
        raise UsageError("Null calibration needs at least one gene group.")
    provenance = (
        evidence.provenance if evidence and evidence.provenance else source.provenance
    )
    sex = provenance.sex if provenance else None
    _require_complete_calls(source, sex)
    model_path = params.exons or mane_model(params.ref)
    current_result().add_input(model_path, "calibration_exon_model")
    expected_by_transcript: dict[str, float] | None = None
    if use_expected_counts:
        expected_path, expected_by_transcript = expected_syn(params.ref)
        current_result().add_input(expected_path, "calibration_expected_counts")
    model = ec.load_model(model_path)
    input_names = ec.contig_names(model, source.lengths)
    names = input_names
    if evidence is not None:
        names = {
            chrom: name
            for chrom, name in ec.contig_names(model, evidence.lengths).items()
            if chrom in input_names
        }
    else:
        warn(
            "panel_calibration_nominal_cds",
            "No gVCF is available; null matching uses model CDS length, not measured callability.",
        )
    opportunities, excluded = calibration.opportunities(
        model,
        names,
        evidence,
        thresholds,
        sex=sex,
    )
    filters: dict[str, Mapping[str, Any]] = {}
    dosage: dict[str, Mapping[str, float]] = {}
    filter_keys = {}
    cnv_filter_keys = {}
    cnv_scoring_keys = set()
    for group in groups:
        key = json.dumps([group["filters"], group.get("dosage", {})], sort_keys=True)
        filters[key] = group["filters"]
        dosage[key] = group.get("dosage", {})
        filter_keys[group["id"]] = key
        if panel_cnv.enabled(group):
            cnv_key = key if audit.weighted(filters[key], dosage[key]) else f"cnv:{key}"
            cnv_filter_keys[group["id"]] = cnv_key
            filters[cnv_key] = filters[key]
            dosage[cnv_key] = dosage[key]
            cnv_scoring_keys.add(cnv_key)
    calibrator = Calibrator(
        opportunities,
        excluded,
        filters,
        filter_keys,
        cnv_filter_keys,
        {key: Counter() for key in filters},
        dosage,
        {
            key: defaultdict(list)
            for key, rules in filters.items()
            if key in cnv_scoring_keys or audit.weighted(rules, dosage[key])
        },
        {
            key: Counter()
            for key, rules in filters.items()
            if rules.get("exclude_clinvar_benign")
        },
        "callable_cds_bp" if evidence else "cds_bp",
        {
            "min_depth": thresholds.min_depth,
            "min_gq": thresholds.min_gq,
            "quality_model": evidence.quality_model,
        }
        if evidence
        else None,
        {
            gene: expected_by_transcript[opp.transcript]
            for gene, opp in opportunities.items()
            if opp.transcript in expected_by_transcript
        }
        if expected_by_transcript is not None
        else None,
    )
    if evidence:
        current_result().add_metrics(
            calibration_min_depth=thresholds.min_depth,
            calibration_min_gq=thresholds.min_gq,
            calibration_quality_model=evidence.quality_model,
        )
    return calibrator, model
