"""Read an opt-in panel audit from the sample's existing result manifests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from wgsextract_cli.core.panel_spec import DISCLAIMER
from wgsextract_cli.core.report_documents import ReportPanelAuditDocument

from .context import (
    NOT_ASSESSED,
    Source,
    _command,
    _Context,
    _finding,
    _section,
    latest,
)
from .rendering import RESEARCH_ONLY


def _objects(value: object) -> list[Mapping[str, Any]]:
    return (
        [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, list)
        else []
    )


def _group_text(group: Mapping[str, Any]) -> str:
    text = str(group.get("state") or "not assessed").replace("_", " ")
    calibration = group.get("calibration")
    if not isinstance(calibration, Mapping) or calibration.get("state") != "calibrated":
        return text
    p = calibration.get("upper_tail_p")
    if not isinstance(p, (float, int)) or isinstance(p, bool) or not 0 <= p <= 1:
        return text
    copies = calibration.get("observed_allele_copies")
    genes = calibration.get("target_genes")
    if type(copies) is not int or copies < 0 or type(genes) is not int or genes < 1:
        return text
    basis = (
        "callable CDS" if calibration.get("basis") == "callable_cds_bp" else "model CDS"
    )
    copy_label = "copy" if copies == 1 else "copies"
    gene_label = "gene" if genes == 1 else "genes"
    limitation = "; underpowered" if calibration.get("underpowered") else ""
    return (
        f"{text}; same-genome {basis} null for {copies} qualifying allele {copy_label} "
        f"across {genes} eligible {gene_label}: empirical upper-tail p={p:.3g}{limitation}"
    )


def _panel_key(source: Source) -> tuple[object, ...]:
    paths = tuple(
        source.locate(path)
        for record in source.records("inputs")
        if record.get("role") == "panel" and isinstance(path := record.get("path"), str)
    )
    return paths or (source.manifest,)


def panel_audit(ctx: _Context) -> ReportPanelAuditDocument | None:
    match = _command("panel audit")
    source = latest(ctx.sources, match)
    if source is None:
        return None
    panel_key = _panel_key(source)
    source = ctx.latest(lambda run: match(run) and _panel_key(run) == panel_key)
    assert source is not None
    section: ReportPanelAuditDocument = {
        **_section("panel_audit", [source], ctx),
        "research_only": True,
        "disclaimer": DISCLAIMER,
    }
    section["warnings"] = [
        warning
        for warning in section["warnings"]
        if warning["code"] != "panel_research_only"
    ]
    if source.status != "ok":
        return section
    path = source.output("panel_audit")
    try:
        if path is None:
            raise ValueError("The audit JSON is missing.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("schema") != "wgsextract.panel_audit/1"
            or data.get("research_only") is not True
            or not isinstance(data.get("sample"), str)
            or data["sample"] != source.metrics.get("sample")
        ):
            raise ValueError("The audit JSON does not match its manifest.")
    except (OSError, ValueError) as error:
        section["status"] = "failed"
        section["warnings"].append(
            {"code": "panel_audit_unavailable", "message": str(error)}
        )
        return section

    groups = _objects(data.get("groups"))
    sites, watches = _objects(data.get("sites")), _objects(data.get("haplotypes"))
    panel = data.get("panel")
    name = panel.get("name") if isinstance(panel, Mapping) else None
    prefix = f"{name}: " if isinstance(name, str) and name else ""
    section["summary"] = (
        f"{prefix}{len(groups)} gene group(s), {len(sites)} watched locus/loci, "
        f"{len(watches)} haplotype watch(es). See the audit for genotypes and evidence."
    )
    for group in groups:
        label = str(group.get("name") or group.get("id") or "Panel gene group")
        copies = sum(
            count
            for gene in _objects(group.get("genes"))
            if type(count := gene.get("qualifying_allele_copies")) is int and count >= 0
        )
        section["findings"].append(
            _finding(
                "panel_audit",
                label,
                f"{copies} qualifying allele copies",
                detail=_group_text(group),
                confidence=NOT_ASSESSED,
                reasons=[],
                basis="Panel filter matches are not a clinical classification.",
                evidence=ctx.rel(path),
                region=None,
                manifest=ctx.rel(source.manifest),
                disclaimer=RESEARCH_ONLY,
            )
        )
    if not groups:
        section["findings"].append(
            _finding(
                "panel_audit",
                "Watched genotypes",
                "See the audit results",
                confidence=NOT_ASSESSED,
                reasons=[],
                basis="Locus and allele-match confidence is recorded in the audit.",
                evidence=ctx.rel(path),
                region=None,
                manifest=ctx.rel(source.manifest),
                disclaimer=RESEARCH_ONLY,
            )
        )
    return section
