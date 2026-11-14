"""ClinVar assertion support, separate from the sequencing call's confidence."""

import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import unquote

IDENTITY = "WGSE_CLINVAR_ID"
STARS = "WGSE_CLINVAR_STARS"
TIER = "WGSE_CLINVAR_REVIEW"
COMPONENTS = "WGSE_MNV_COMPONENTS"
ORIGIN = "WGSE_ORIGINAL"
COMPONENT_CONF = "WGSE_MNV_COMPONENT_CONF"
COMPONENT_REASONS = "WGSE_MNV_COMPONENT_REASONS"


def review_evidence(info: Mapping[str, object]) -> dict[str, object]:
    status = str(info.get("CLNREVSTAT") or "")
    if "practice_guideline" in status:
        stars, tier = 4, "practice guideline"
    elif "reviewed_by_expert_panel" in status:
        stars, tier = 3, "expert panel"
    elif "multiple_submitters" in status and "no_conflicts" in status:
        stars, tier = 2, "multiple submitters, no conflicts"
    elif "criteria_provided" in status and "no_assertion_criteria" not in status:
        stars, tier = 1, "criteria provided"
    else:
        stars, tier = 0, "no assertion criteria"
    if not status or status == ".":
        tier = "review status unavailable"
    conflict = str(info.get("CLNSIGCONF") or "")
    conflicting = "conflict" in str(info.get("CLNSIG") or "").lower() or (
        bool(conflict) and conflict != "."
    )
    return {
        "id": info.get(IDENTITY) or None,
        "review_status": status or None,
        "stars": stars if status and status != "." else None,
        "review_tier": tier,
        "conflict": conflicting,
        "conflict_evidence": conflict or None,
    }


def nonredundant_calls(calls: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Subtract only the component copies established by a composed MNV."""

    def key(call: Mapping[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(call.get("chrom")).removeprefix("chr"),
            str(call.get("pos")),
            str(call.get("ref")),
            str(call.get("alt")),
        )

    covered: dict[tuple[tuple[str, str, str, str], str], tuple[str, set[int]]] = {}
    annotated = [dict(call) for call in calls]
    for call in annotated:
        try:
            parts = json.loads(unquote(str(call.get(COMPONENTS) or "[]")))
            if not parts:
                continue
            fmt = dict(
                zip(
                    parts[0]["format"].split(":"),
                    parts[0]["sample"].split(":"),
                    strict=False,
                )
            )
            ps = fmt["PS"]
            haplotypes = {
                h
                for h, allele in enumerate(str(call.get("genotype", "")).split("|"))
                if allele == "1"
            }
            call["_mnv_phase_set"] = ps
            for component in parts:
                for gene in call.get("_report_gene_symbols", [""]):
                    covered.setdefault((key(component), gene), (ps, set()))[1].update(
                        haplotypes
                    )
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            continue
    result = []
    for call in annotated:
        genes = call.get("_report_gene_symbols", [""])
        if not genes:
            result.append(call)
            continue
        groups: dict[tuple[str, str], list[str]] = {}
        for gene in genes:
            residual, phase = "", ""
            coverage = covered.get((key(call), gene))
            if coverage is not None:
                ps, haplotypes = coverage
                gt = str(call.get("genotype", "")).split("|")
                if len(gt) == 2 and set(gt) <= {"0", "1"}:
                    remaining = [
                        "1" if allele == "1" and h not in haplotypes else "0"
                        for h, allele in enumerate(gt)
                    ]
                    if "1" not in remaining:
                        continue
                    residual, phase = "|".join(remaining), ps
            groups.setdefault((residual, phase), []).append(gene)
        for (residual, phase), retained_genes in groups.items():
            retained = dict(call)
            if "_report_gene_symbols" in call:
                retained["_report_gene_symbols"] = retained_genes
            if residual:
                retained["_gene_genotype"] = residual
                retained["_mnv_phase_set"] = phase
                retained["_mnv_residual_copies"] = residual.split("|").count("1")
            result.append(retained)
    return result
