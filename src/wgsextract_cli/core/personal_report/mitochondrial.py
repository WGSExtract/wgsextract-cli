"""Report section: mtDNA heteroplasmy at reviewed variants (#1175).

Reads the mutserve evidence ``lineage mt-haplogroup --contamination`` kept
(core/mt_heteroplasmy.py) and lists, separately, its technical support, the
reviewed disease variants and the MT-RNR1 aminoglycoside-risk alleles.
Mixtures stay mixtures: a call's level is reported, never a diploid genotype.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wgsextract_cli.core import mt_heteroplasmy as mt
from wgsextract_cli.core import variant_confidence
from wgsextract_cli.core.clinical_evidence import IDENTITY, review_evidence
from wgsextract_cli.core.report_documents import (
    ReportFindingDocument,
    ReportMitochondrialDocument,
    ReportMitochondrialFields,
    ReportVariantDocument,
    ReportWarningDocument,
)
from wgsextract_cli.core.report_mitochondrial_documents import (
    ReportMitoResourceDocument,
    ReportMitoSiteDocument,
    ReportMitoTechnicalDocument,
    ReportMitoThresholdsDocument,
)

from .context import (
    NOT_ASSESSED,
    Source,
    _contaminated,
    _Context,
    _finding,
    _float,
    _int,
    _missing,
    _section,
)

MT_COMMAND = "lineage mt-haplogroup"
ADVISORY = (
    "Educational, for review with a clinician; not a prescribing instruction. "
    "Confirm any detected variant in an accredited clinical laboratory before "
    "acting on it. Heteroplasmy levels are measured in the sequenced sample "
    "(usually blood) and can differ in other tissues and over time. 'Not "
    "detected' rules out heteroplasmy of 10% or more only; 'not assessed' is "
    "unknown, not a negative result."
)
_NOTES = {
    "aminoglycoside": "CPIC knows no heteroplasmy level at which aminoglycosides "
    "are safe, and treats a detected MT-RNR1 increased-risk variant at any level "
    "like a homoplasmic one. Share with a clinician before aminoglycoside "
    "antibiotics are considered.",
    "disease": "Whether and how a pathogenic mtDNA variant causes disease depends "
    "on its heteroplasmy level and the tissue; a level in blood need not match "
    "affected tissues.",
}
_BASIS = "mutserve level, depth and strand support at the reviewed variant"


def _heteroplasmy_run(source: Source) -> bool:
    if source.command != MT_COMMAND:
        return False
    return source.status != "ok" or any(
        record.get("role") == mt.CALLS_ROLE for record in source.records("outputs")
    )


def _confidence(
    state: str, reasons: list[str], contamination: object, contaminated: bool
) -> tuple[str, list[str]]:
    if state == mt.NOT_ASSESSED:
        return NOT_ASSESSED, reasons
    if state == mt.WEAK or "mt_low_depth" in reasons:
        return variant_confidence.LOW, reasons
    if state == mt.NOT_DETECTED:
        return variant_confidence.HIGH, reasons
    if contamination == "YES":
        label = (
            variant_confidence.LOW
            if state == mt.HETEROPLASMIC
            else variant_confidence.MODERATE
        )
        return label, [*reasons, "mt_contamination"]
    if contaminated:
        return variant_confidence.MODERATE, [*reasons, "sample_contaminated"]
    return variant_confidence.HIGH, reasons


def _site(
    variant: Mapping[str, Any], found: mt.Assessment, label: str, reasons: list[str]
) -> ReportMitoSiteDocument:
    clinvar = variant["clinvar"]
    cpic = variant.get("cpic") or {}
    evidence = review_evidence({"CLNREVSTAT": clinvar["review_status"]})
    return ReportMitoSiteDocument(
        group=variant["group"],
        gene=variant["gene"],
        hgvs=variant["hgvs"],
        pos=int(variant["pos"]),
        ref=variant["ref"],
        alt=variant["alt"],
        state=found.state,
        level=found.level,
        depth=found.depth,
        depth_fwd=found.depth_fwd,
        depth_rev=found.depth_rev,
        level_fwd=found.level_fwd,
        level_rev=found.level_rev,
        confidence=label,
        reasons=reasons,
        clinvar_id=str(clinvar["variation_id"]),
        clinvar_classification=clinvar["classification"],
        clinvar_review_status=clinvar["review_status"],
        clinvar_stars=evidence.get("stars"),
        conditions=list(clinvar["conditions"]),
        cpic_phenotype=cpic.get("phenotype"),
        cpic_evidence=cpic.get("evidence"),
    )


def _technical(
    ctx: _Context, source: Source, details: Mapping[str, Any]
) -> ReportMitoTechnicalDocument:
    reference = details.get("mt_reference") or {}
    metrics = source.metrics
    contamination = metrics.get("mt_contamination")
    return ReportMitoTechnicalDocument(
        caller=str(details.get("caller") or "mutserve"),
        version=details.get("version"),
        settings=str(details.get("settings") or ""),
        mt_reference=reference.get("origin"),
        contig=reference.get("contig"),
        reference_md5=reference.get("md5"),
        median_depth=_float(metrics.get("mt_median_depth")),
        min_depth_fraction=_float(metrics.get("mt_min_depth_fraction")),
        homoplasmies=_int(metrics.get("mt_homoplasmies")),
        heteroplasmies=_int(metrics.get("mt_heteroplasmies")),
        contamination=str(contamination) if contamination else None,
        calls=ctx.rel(source.output(mt.CALLS_ROLE)),
        site_evidence=ctx.rel(source.output(mt.SITE_EVIDENCE_ROLE)),
    )


def _resource(table: Mapping[str, Any]) -> ReportMitoResourceDocument:
    clinvar, cpic = table["sources"]["clinvar"], table["sources"]["cpic"]
    return ReportMitoResourceDocument(
        id=table["id"],
        name=table["name"],
        clinvar_release=clinvar["release"],
        clinvar_url=clinvar["url"],
        clinvar_md5=clinvar["md5"],
        clinvar_selection=clinvar["selection"],
        cpic_citation=cpic["citation"],
        cpic_url=cpic["url"],
        heteroplasmy_basis=cpic["heteroplasmy"],
        variants=len(table["variants"]),
    )


def _value(variant: Mapping[str, Any], state: str) -> str:
    if state == mt.WEAK:
        return "unconfirmed: weak evidence"
    if variant["group"] == "aminoglycoside":
        return f"{variant['cpic']['phenotype']} (CPIC)"
    return str(variant["clinvar"]["classification"]).replace("|", ", ")


def _detail(variant: Mapping[str, Any], found: mt.Assessment) -> str:
    level = f" at {found.level:.1%}" if found.level is not None else ""
    reads = f" of {found.depth:,} reads" if found.depth is not None else ""
    head = f"{found.state.replace('_', ' ')}{level}{reads}"
    if variant["group"] == "aminoglycoside":
        return f"{head}; CPIC evidence {variant['cpic']['evidence']}"
    conditions = ", ".join(variant["clinvar"]["conditions"][:3])
    return f"{head}; {conditions}" if conditions else head


def _warn_unassessed(
    section: ReportMitochondrialDocument, origin: object, evidence: object
) -> None:
    if origin not in mt.ASSESSABLE_ORIGINS:
        section["warnings"].append(
            ReportWarningDocument(
                code="mt_reference_unconfirmed",
                message="The run's mtDNA reference isn't confirmed as the rCRS "
                "or CHM13v2.0's chrM, so no reviewed variant is assessed. Rerun "
                "on an alignment to hg38, hs37d5 or CHM13v2.0.",
            )
        )
    elif evidence is None:
        section["warnings"].append(
            ReportWarningDocument(
                code="mt_evidence_missing",
                message="mutserve's calls or read table are missing or "
                "unreadable, so no reviewed variant is assessed. Rerun "
                f"{MT_COMMAND} --contamination.",
            )
        )


def mitochondrial(ctx: _Context) -> ReportMitochondrialDocument:
    source = ctx.latest(_heteroplasmy_run)
    section: ReportMitochondrialDocument = {
        **_section("mitochondrial", [source], ctx),
        **ReportMitochondrialFields(),
    }
    if source is None or source.status != "ok":
        command = ctx.command_for(MT_COMMAND, ctx.alignment, "cram")
        _missing(
            section,
            "mtDNA heteroplasmy calls (mutserve; needs a BAM or CRAM)",
            f"{command} --contamination",
        )
        return section
    table = mt.reviewed_table()
    found_details = source.details.get("heteroplasmy")
    details = found_details if isinstance(found_details, Mapping) else {}
    origin = (details.get("mt_reference") or {}).get("origin")
    evidence = mt.load_evidence(
        source.output(mt.CALLS_ROLE), source.output(mt.SITE_EVIDENCE_ROLE)
    )
    _warn_unassessed(section, origin, evidence)
    technical = _technical(ctx, source, details)
    contaminated = _contaminated(ctx, source.sample_files()) is not None
    sites: list[ReportMitoSiteDocument] = []
    findings: list[ReportFindingDocument] = []
    contig = technical["contig"] or "chrM"
    for variant in table["variants"]:
        found = mt.assess(variant, evidence, origin)
        label, reasons = _confidence(
            found.state, found.reasons, technical["contamination"], contaminated
        )
        sites.append(_site(variant, found, label, reasons))
        if found.state not in (*mt.DETECTED, mt.WEAK):
            continue
        clinvar = variant["clinvar"]
        findings.append(
            _finding(
                "mitochondrial",
                f"{variant['gene']} {variant['hgvs']}",
                _value(variant, found.state),
                detail=_detail(variant, found),
                gene=variant["gene"],
                note=_NOTES[variant["group"]],
                confidence=label,
                reasons=reasons,
                basis=_BASIS,
                evidence=technical["calls"],
                region=f"{contig}:{mt.evidence_position(int(variant['pos']), origin)}",
                manifest=ctx.rel(source.manifest),
                variant=ReportVariantDocument(
                    chrom=contig if origin == mt.RCRS else "rCRS",
                    pos=int(variant["pos"]),
                    ref=variant["ref"],
                    alt=variant["alt"],
                    genotype=None,
                    zygosity=found.state if found.state in mt.DETECTED else None,
                    depth=found.depth,
                    genotype_quality=None,
                    alt_fraction=found.level,
                    clinical_evidence=review_evidence(
                        {
                            "CLNREVSTAT": clinvar["review_status"],
                            IDENTITY: clinvar["variation_id"],
                        }
                    ),
                ),
            )
        )
    counts = {state: sum(s["state"] == state for s in sites) for state in mt.STATES}
    section["advisory"] = ADVISORY
    section["technical"] = technical
    section["thresholds"] = ReportMitoThresholdsDocument(
        homoplasmic=mt.HOMOPLASMIC_LEVEL,
        supported_heteroplasmy=mt.SUPPORTED_LEVEL,
        weak_evidence=mt.EVIDENCE_LEVEL,
        min_depth=mt.MIN_DEPTH,
    )
    section["resource"] = _resource(table)
    section["sites"] = sites
    section["counts"] = counts
    section["findings"] = findings
    section["summary"] = _summary(technical, counts, sites)
    return section


def _summary(
    technical: ReportMitoTechnicalDocument,
    counts: Mapping[str, int],
    sites: list[ReportMitoSiteDocument],
) -> str:
    depth = technical["median_depth"]
    called = (
        f"{technical['caller']} {technical['version'] or ''}".strip()
        + f" on {technical['mt_reference'] or 'an unconfirmed mtDNA reference'}"
        + (f", median depth {depth:,.0f}x" if depth is not None else "")
        + f": {technical['homoplasmies'] or 0} homoplasmies, "
        f"{technical['heteroplasmies'] or 0} heteroplasmies."
    )
    reviewed = (
        f" Reviewed variants: {counts[mt.HOMOPLASMIC] + counts[mt.HETEROPLASMIC]} "
        f"detected, {counts[mt.WEAK]} with weak evidence, "
        f"{counts[mt.NOT_DETECTED]} not detected, {counts[mt.NOT_ASSESSED]} not "
        "assessed."
    )
    listed = ""
    for kind, states in (("Detected", mt.DETECTED), ("Weak evidence", (mt.WEAK,))):
        named = [
            f"{s['gene']} {s['hgvs']} ("
            + (f"{s['state']}, " if s["state"] in mt.DETECTED else "")
            + (f"{s['level']:.1%}" if s["level"] is not None else "level unknown")
            + ")"
            for s in sites
            if s["state"] in states
        ]
        if named:
            listed += f" {kind}: {', '.join(named)}."
    return called + reviewed + listed
