"""ClinVar assertion support, separate from the sequencing call's confidence."""

from collections.abc import Mapping

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
