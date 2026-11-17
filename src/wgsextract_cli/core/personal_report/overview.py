"""Personal-report overview support."""

from __future__ import annotations

import csv

from wgsextract_cli.core import (
    contamination,
    read_scope,
)
from wgsextract_cli.core.report_documents import (
    ReportAncestryComponentDocument,
    ReportAncestryDocument,
    ReportAncestryFields,
    ReportFactDocument,
    ReportMetricDocument,
    ReportMicroarrayDocument,
    ReportMicroarrayFields,
    ReportMicroarrayFileDocument,
    ReportOverviewDocument,
    ReportSectionDocument,
    ReportWarningDocument,
)

from .context import (
    _command,
    _contamination_fact,
    _Context,
    _fact_text,
    _finding,
    _missing,
    _quality_label,
    _section,
)
from .rendering import (
    _number,
)

_OVERVIEW_FACTS = (
    ("sex", "Sex"),
    ("platform", "Platform"),
    ("build", "Build"),
    ("mean_coverage", "Mean coverage"),
    ("target_depths", "Depth by target"),
    ("read_length", "Read length"),
)
_INFO_FACTS = (
    ("sex", "sex"),
    ("mean_coverage", "ard_mapped"),
    ("read_length", "avg_read_len"),
)
_QC_METRICS = (
    ("sequencer", "Sequencer", ""),
    ("ard_mapped", "Mean depth (mapped)", "x"),
    ("reads_mapped_pct", "Reads mapped", "%"),
    ("duplicate_pct", "Duplicates", "%"),
    ("avg_read_len", "Mean read length", " bp"),
    ("avg_insert_size", "Mean insert size", " bp"),
    ("size_gb", "File size", " GB"),
)
_MICROARRAY_ROLES = ("combined_kit", "vendor_kit", "aadr")


def _overview(ctx: _Context) -> ReportOverviewDocument:
    info = ctx.latest(_command("info"))
    run = ctx.latest(_command("qc contamination"))
    run = run if run is not None and run.status == "ok" else None
    repeat_run = ctx.latest(_command("typing str"))
    repeat_run = (
        repeat_run if repeat_run is not None and repeat_run.status == "ok" else None
    )
    trio = ctx.latest(_command("vcf trio"))
    trio = trio if trio is not None and trio.status == "ok" else None
    section = ReportOverviewDocument(
        **_section("overview", [info, run, trio, repeat_run], ctx), facts=[], qc=[]
    )
    facts: list[ReportFactDocument] = []
    for name, label in _OVERVIEW_FACTS:
        fact = ctx.fact(name)
        if fact is None:
            continue
        value = fact.get("value")
        facts.append(
            ReportFactDocument(
                name=name,
                label=label,
                value=value,
                text=_fact_text(name, value),
                source=fact.get("source"),
                method=fact.get("method"),
                confidence=fact.get("confidence"),
                file=fact.get("file"),
                at=fact.get("at"),
            )
        )
    if info is not None and info.status == "ok":
        # Outside a sample workspace nothing is recorded; `info` still says.
        known = {f["name"] for f in facts}
        for name, key in _INFO_FACTS:
            value = info.metrics.get(key)
            if name in known or value in (None, "", "Unknown"):
                continue
            facts.append(
                ReportFactDocument(
                    name=name,
                    label=dict(_OVERVIEW_FACTS)[name],
                    value=value,
                    text=_fact_text(name, value),
                    source="info",
                    method="info --detailed metrics (no sample workspace)",
                    confidence=None,
                    file=next(iter(map(ctx.rel, info.files("inputs"))), None),
                    at=info.finished or None,
                )
            )
    estimate = _contamination_fact(ctx, run)
    if estimate is not None:
        facts.append(estimate)
    section["facts"] = facts
    freemix = estimate["value"] if estimate else None
    threshold = contamination.max_contamination()
    warned = any(w["code"] == contamination.WARNING for w in section["warnings"])
    if (
        isinstance(freemix, (int, float))
        and threshold
        and freemix >= threshold
        and not warned
    ):
        section["warnings"].append(
            ReportWarningDocument(
                code=contamination.WARNING,
                message=f"qc contamination estimated {freemix:.1%} of the "
                "reads are another person's (FREEMIX, at or above "
                f"qc_max_contamination {threshold:.1%}): every finding below "
                f"may be affected. See {contamination.GUIDE}.",
            )
        )
    qc: list[ReportMetricDocument] = []
    if info is not None and info.status == "ok":
        for key, label, unit in _QC_METRICS:
            value = info.metrics.get(key)
            if value in (None, ""):
                continue
            qc.append(
                ReportMetricDocument(
                    name=key, label=label, value=value, text=_number(value, unit)
                )
            )
    section["qc"] = qc
    if facts:
        section["status"] = "ok"
    if info is None or info.status != "ok":
        _missing(
            section,
            "QC metrics and sex (info --detailed)",
            ctx.command_for("info --detailed", ctx.alignment, "cram"),
        )
    if ctx.alignment and freemix is None:
        _missing(
            section,
            "DNA contamination check (qc contamination)",
            ctx.command_for("qc contamination", ctx.alignment, "cram"),
        )
    section["summary"] = (
        ", ".join(f"{f['label']} {f['text']}" for f in facts[:4])
        or "No facts recorded."
    )
    return section


def _lineage(ctx: _Context) -> ReportSectionDocument:
    y = ctx.latest(_command("lineage y-haplogroup"))
    mt = ctx.latest(_command("lineage mt-haplogroup"))
    section = _section("lineage", [y, mt], ctx)
    from wgsextract_cli.commands._lineage_links import tree_links

    sex = ctx.fact("sex")
    female = bool(sex and str(sex.get("value")) == "Female")
    for source, what, mitochondrial, region in (
        (y, "Y-DNA haplogroup", False, "chrY"),
        (mt, "mtDNA haplogroup", True, "chrM"),
    ):
        command = "lineage mt-haplogroup" if mitochondrial else "lineage y-haplogroup"
        if source is None or source.status != "ok":
            if not mitochondrial and female:
                continue
            _missing(section, what, ctx.command_for(command, ctx.alignment, "cram"))
            continue
        haplogroup = str(source.metrics.get("haplogroup") or "")
        if not haplogroup:
            continue
        quality_key = "haplogroup_quality" if mitochondrial else "qc_score"
        quality = source.metrics.get(quality_key)
        label, reasons = _quality_label(quality, source.warnings())
        marker = source.metrics.get("haplogroup_marker")
        detail = ", ".join(
            x
            for x in (
                f"marker {marker}" if marker else "",
                f"quality {quality:g}" if isinstance(quality, (int, float)) else "",
            )
            if x
        )
        section["findings"].append(
            _finding(
                "lineage",
                what,
                haplogroup,
                detail=detail or None,
                confidence=label,
                reasons=reasons,
                basis="Haplogrep quality" if mitochondrial else "Yleaf QC-score",
                evidence=ctx.rel(source.output("haplogroup_report")),
                region=region,
                manifest=ctx.rel(source.manifest),
                links=tree_links(haplogroup, mitochondrial=mitochondrial),
            )
        )
    summary = "; ".join(f"{f['label']} {f['value']}" for f in section["findings"])
    if female and (y is None or y.status != "ok"):
        summary = "; ".join(
            x for x in (summary, "Y-DNA: none (recorded sex Female)") if x
        )
    section["summary"] = summary
    return section


ANCESTRY_NOTE = (
    "Shares of the sample's genome best matched by reference components "
    "learned from the HGDP and 1000 Genomes samples. Each is named after the "
    "reference groups that carry it most; they are not ethnicities, and a "
    "component is shared by many present-day populations."
)


def _ancestry(ctx: _Context) -> ReportAncestryDocument:
    source = ctx.latest(_command("qc ancestry"))
    section: ReportAncestryDocument = {
        **_section("ancestry", [source], ctx),
        **ReportAncestryFields(),
        "note": ANCESTRY_NOTE,
        "components": [],
    }
    if source is None or source.status != "ok":
        _missing(
            section,
            "Ancestry proportions (qc ancestry)",
            ctx.command_for("qc ancestry", ctx.alignment or ctx.vcf, "cram"),
        )
        return section
    table = source.output("ancestry_table")
    try:
        with open(table or "", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
    except (OSError, csv.Error):
        rows = []
    for row in rows:
        try:
            section["components"].append(
                ReportAncestryComponentDocument(
                    k=int(row["k"]),
                    name=row["component"],
                    region=row["region"],
                    proportion=float(row["proportion"]),
                    lower=float(row["lower_95"]),
                    upper=float(row["upper_95"]),
                    reference_populations=row["reference_populations"],
                )
            )
        except (KeyError, ValueError):
            continue
    section["metrics"] = {
        k: source.metrics[k]
        for k in ("ancestry_sites", "ancestry_depth", "ancestry_confidence")
        if k in source.metrics
    }
    section["evidence"] = ctx.rel(table)
    top = [c for c in section["components"] if c["k"] == 7 and c["proportion"] >= 0.01]
    section["summary"] = (
        ", ".join(f"{c['name']} {c['proportion']:.0%}" for c in top[:4])
        or "No proportions recorded."
    )
    return section


def _microarray(ctx: _Context) -> ReportMicroarrayDocument:
    source = ctx.latest(_command("microarray"))
    section: ReportMicroarrayDocument = {
        **_section("microarray", [source], ctx),
        **ReportMicroarrayFields(),
        "files": [],
    }
    if source is None or source.status != "ok":
        _missing(
            section,
            "Consumer microarray files (23andMe, AncestryDNA, ...)",
            ctx.command_for("microarray", ctx.alignment or ctx.vcf, "cram"),
        )
        return section
    for record in source.records("outputs"):
        if record.get("role") not in _MICROARRAY_ROLES or not record.get("path"):
            continue
        path = source.locate(str(record["path"]))
        metrics = record.get("metrics") or {}
        section["files"].append(
            ReportMicroarrayFileDocument(
                file=ctx.rel(path),
                role=record["role"],
                format=metrics.get("format")
                or ("CombinedKit" if record.get("role") == "combined_kit" else None),
                exists=read_scope.may_follow(path) and path.exists(),
                sites=metrics.get("sites"),
                called=metrics.get("called"),
                no_call_rate=metrics.get("no_call_rate"),
            )
        )
    section["metrics"] = {
        k: source.metrics[k]
        for k in ("autosomal_called", "autosomal_homozygosity")
        if k in source.metrics
    }
    count = len(section["files"])
    section["summary"] = f"{count} file{'s' * (count != 1)} produced."
    return section
