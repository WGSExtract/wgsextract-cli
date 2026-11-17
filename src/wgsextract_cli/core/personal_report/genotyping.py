"""Personal-report genotyping support."""

from __future__ import annotations

from typing import Any

from wgsextract_cli.core import (
    variant_confidence,
)
from wgsextract_cli.core.report_documents import (
    ReportPgxAlternativeDocument,
    ReportPgxDocument,
    ReportWarningDocument,
)

from .context import (
    NOT_ASSESSED,
    Source,
    _command,
    _command_or_role,
    _Context,
    _finding,
    _labelled_calls,
    _missing,
    _section,
    _worst,
)
from .rendering import (
    _number,
)


def _pharmacogenomics(ctx: _Context) -> dict[str, Any]:
    pharmcat = ctx.latest(_command_or_role("vcf pharmcat", "pharmcat_phenotypes"))
    cyrius = ctx.latest(_command("typing cyp2d6"))
    hla = ctx.latest(_command("typing hla"))
    section = _section("pharmacogenomics", [pharmcat, cyrius, hla], ctx)
    if pharmcat is not None and pharmcat.status == "ok":
        _pharmcat_findings(section, pharmcat, ctx)
    else:
        _missing(
            section,
            "PharmCAT diplotypes and phenotypes (GRCh38 VCF)",
            ctx.command_for("vcf pharmcat", ctx.vcf, "vcf.gz"),
        )
    if cyrius is not None and cyrius.status == "ok":
        _cyrius_finding(section, cyrius, ctx)
    else:
        _missing(
            section,
            "CYP2D6 star alleles (Cyrius, ~30x WGS)",
            ctx.command_for("typing cyp2d6", ctx.alignment, "cram"),
        )
    if hla is not None and hla.status == "ok":
        _hla_findings(section, hla, ctx)
    else:
        _missing(
            section,
            "HLA-A and HLA-B alleles (T1K)",
            ctx.command_for("typing hla", ctx.alignment, "cram"),
        )
    gene_statuses: dict[str, str] = {}
    priority = {"resolved": 0, "ambiguous": 1, "no_call": 2}
    for finding in section["findings"]:
        status = (finding.get("pgx") or {}).get(
            "status",
            "no_call" if "no_call" in finding["confidence_reasons"] else "resolved",
        )
        previous = gene_statuses.get(finding["gene"], "no_call")
        gene_statuses[finding["gene"]] = min(
            (previous, status), key=priority.__getitem__
        )
    statuses: dict[str, set[str]] = {}
    for gene, status in gene_statuses.items():
        statuses.setdefault(status, set()).add(gene)
    section["summary"] = "; ".join(
        f"{len(genes)} {status.replace('_', '-')} gene(s)"
        for status, genes in sorted(statuses.items())
    )
    return section


def _pharmcat_findings(section: dict[str, Any], source: Source, ctx: _Context) -> None:
    from wgsextract_cli.commands.vcf.pharmcat import summarize

    phenotypes = source.output("pharmcat_phenotypes")
    try:
        rows = summarize(str(phenotypes)) if phenotypes else []
    except (OSError, ValueError) as e:
        section["warnings"].append(
            ReportWarningDocument(
                code="unreadable_evidence", message=f"PharmCAT phenotypes: {e}"
            )
        )
        rows = []
    calls: dict[str, list[dict[str, Any]]] = {}
    for call in _labelled_calls(source, "pgx_calls", "confidence_table")[0]:
        for gene in str(call.get("gene") or "").split(","):
            if gene:
                calls.setdefault(gene, []).append(call)
    evidence = source.output("pharmcat_report", "pharmcat_phenotypes")
    for row in rows:
        gene, diplotype, phenotype = row.gene, row.diplotype, row.phenotype
        gene_calls = calls.get(gene, [])
        if gene_calls:
            label = _worst(str(c.get("confidence")) for c in gene_calls)
            reasons = sorted(
                {
                    r
                    for c in gene_calls
                    if c.get("confidence") == label
                    for r in c["reasons"]
                }
            )
            basis = f"weakest of {len(gene_calls)} labelled call(s) in the gene"
        else:
            label, reasons = NOT_ASSESSED, ["no_labelled_calls"]
            basis = "no labelled calls in the gene"
        if row.status != "resolved":
            label = variant_confidence.LOW
            reasons = sorted({*reasons, row.status})
        finding = _finding(
            "pharmacogenomics",
            f"{gene} diplotype (PharmCAT)",
            diplotype,
            detail=phenotype if phenotype != "-" else None,
            gene=gene,
            confidence=label,
            reasons=reasons,
            basis=basis,
            evidence=ctx.rel(evidence),
            region=f"gene {gene}",
            manifest=ctx.rel(source.manifest),
        )
        finding["pgx"] = ReportPgxDocument(
            status=row.status,
            alternatives=[
                ReportPgxAlternativeDocument(
                    diplotype=alternative["diplotype"],
                    phenotype=alternative["phenotype"],
                )
                for alternative in row.alternatives
            ],
            phenotype_consistent=row.phenotype_consistent,
            source="PharmCAT",
        )
        section["findings"].append(finding)


def _cyrius_finding(section: dict[str, Any], source: Source, ctx: _Context) -> None:
    metrics = source.metrics
    genotype = metrics.get("cyp2d6_genotype")
    status = metrics.get("cyp2d6_filter")
    call_status = metrics.get("cyp2d6_call_status")
    depth = metrics.get("median_depth")
    thresholds = variant_confidence.thresholds_from()
    if not genotype:
        label, reasons = variant_confidence.LOW, ["no_call"]
    elif status != "PASS":
        label, reasons = variant_confidence.LOW, ["caller_filter"]
    elif call_status == "unresolved":
        label, reasons = variant_confidence.LOW, ["genotype_unresolved"]
    elif not isinstance(depth, (int, float)):
        label, reasons = variant_confidence.MODERATE, ["depth_unknown"]
    elif depth < thresholds.min_depth:
        label, reasons = variant_confidence.LOW, ["low_depth"]
    elif depth < thresholds.high_depth:
        label, reasons = variant_confidence.MODERATE, ["moderate_depth"]
    else:
        label, reasons = variant_confidence.HIGH, []
    if genotype and call_status is None and label == variant_confidence.HIGH:
        # Runs from before Cyrius's Call_info was recorded can't show the call
        # was a unique match.
        label, reasons = variant_confidence.MODERATE, ["call_info_unknown"]
    # Cyrius's Total_CN, recorded as cyp2d6_copy_number by older runs, counts
    # CYP2D6 and CYP2D7 together.
    copies = metrics.get("cyp2d6_cyp2d7_copy_number", metrics.get("cyp2d6_copy_number"))
    candidates = metrics.get("cyp2d6_raw_star_alleles")
    detail = ", ".join(
        x
        for x in (
            f"filter {status}" if status else "",
            f"Cyrius call {metrics['cyp2d6_call_info']}"
            if metrics.get("cyp2d6_call_info")
            else "",
            f"candidates {candidates}" if candidates else "",
            f"CNV group {metrics['cyp2d6_cnv_group']}"
            if metrics.get("cyp2d6_cnv_group")
            else "",
            f"CYP2D6+CYP2D7 copy number {copies}" if copies is not None else "",
            f"median depth {_number(depth, 'x')}" if depth is not None else "",
        )
        if x
    )
    if not genotype:
        value = "no call"
    elif call_status == "unresolved" or (call_status is None and status != "PASS"):
        value = f"unresolved (Cyrius: {genotype})"
    else:
        value = str(genotype)
    section["findings"].append(
        _finding(
            "pharmacogenomics",
            "CYP2D6 diplotype (Cyrius)",
            value,
            detail=detail or None,
            gene="CYP2D6",
            confidence=label,
            reasons=reasons,
            basis="Cyrius filter, call uniqueness and median depth vs. confidence "
            "thresholds",
            evidence=ctx.rel(source.output("cyp2d6_calls", "cyrius_report")),
            region="gene CYP2D6",
            manifest=ctx.rel(source.manifest),
        )
    )


def _carrier_screening(ctx: _Context) -> dict[str, Any]:
    smn = ctx.latest(_command("typing smn"))
    section = _section("carrier_screening", [smn], ctx)
    if smn is not None and smn.status == "ok":
        _smn_finding(section, smn, ctx)
    else:
        _missing(
            section,
            "SMN1/SMN2 copy number for SMA carrier status (SMNCopyNumberCaller, "
            "~30x short-read WGS)",
            ctx.command_for("typing smn", ctx.alignment, "cram"),
        )
    if section["findings"]:
        section["summary"] = f"{section['findings'][0]['value']}."
    return section


def _smn_finding(section: dict[str, Any], source: Source, ctx: _Context) -> None:
    from wgsextract_cli.commands.genotyping.smn import (
        MAX_COVERAGE_MAD,
        MIN_DEPTH,
    )
    from wgsextract_cli.commands.genotyping.smn import (
        NOT_DIAGNOSIS as SMN_NOT_DIAGNOSIS,
    )

    metrics = source.metrics
    smn1 = metrics.get("smn1_copy_number")
    depth = metrics.get("median_depth")
    mad = metrics.get("coverage_mad")
    carrier, affected = metrics.get("sma_carrier"), metrics.get("sma_affected")
    if smn1 is None:
        label, reasons = variant_confidence.LOW, ["no_call"]
        if any(w.get("code") == "low_coverage" for w in source.warnings()):
            reasons = ["low_coverage"]
    elif carrier is None or affected is None:
        label, reasons = variant_confidence.LOW, ["status_uncertain"]
    elif not str(metrics.get("smn_filter") or "").startswith("PASS"):
        label, reasons = variant_confidence.LOW, ["caller_filter"]
    elif any(w.get("code") == "aligner_not_calibrated" for w in source.warnings()):
        label, reasons = variant_confidence.LOW, ["aligner_not_calibrated"]
    elif isinstance(mad, (int, float)) and mad > MAX_COVERAGE_MAD:
        label, reasons = variant_confidence.LOW, ["uneven_coverage"]
    elif not isinstance(depth, (int, float)):
        label, reasons = variant_confidence.MODERATE, ["depth_unknown"]
    elif depth < MIN_DEPTH:
        label, reasons = variant_confidence.LOW, ["low_depth"]
    else:
        label, reasons = variant_confidence.HIGH, []
    if smn1 is None:
        value = "no call"
    elif smn1 == 0:
        value = "0 SMN1 copies: consistent with SMA" + (
            "" if affected else " (status uncertain)"
        )
    elif smn1 == 1:
        value = "1 SMN1 copy: SMA carrier" + ("" if carrier else " (status uncertain)")
    else:
        value = f"{smn1} SMN1 copies: not a carrier by copy number"
    g27134 = metrics.get("g27134tg_copy_number")
    detail = ", ".join(
        x
        for x in (
            f"SMN1 {smn1}" if smn1 is not None else "",
            f"SMN2 {metrics.get('smn2_copy_number')}" if smn1 is not None else "",
            f"g.27134T>G {g27134}" if g27134 is not None else "",
            f"filter {metrics.get('smn_filter')}" if metrics.get("smn_filter") else "",
            f"median depth {_number(depth, 'x')}" if depth is not None else "",
        )
        if x
    )
    note = SMN_NOT_DIAGNOSIS
    if smn1 == 2:
        note = (
            "Two SMN1 copies can still be a silent (2+0) carrier, with both on one "
            "chromosome"
            + (", which g.27134T>G here makes more likely" if g27134 else "")
            + ". "
            + note
        )
    section["findings"].append(
        _finding(
            "carrier_screening",
            "SMA carrier status (SMN1 copy number, SMNCopyNumberCaller)",
            value,
            detail=detail or None,
            gene="SMN1",
            note=note,
            confidence=label,
            reasons=reasons,
            basis="SMNCopyNumberCaller filter, status and median depth vs. ~30x",
            evidence=ctx.rel(source.output("smn_calls", "smn_report")),
            region="genes SMN1, SMN2",
            manifest=ctx.rel(source.manifest),
        )
    )


def _hla_findings(section: dict[str, Any], source: Source, ctx: _Context) -> None:
    """The HLA-A and HLA-B diplotypes ``typing hla`` called (those PharmCAT
    takes), labelled by T1K's allele qualities."""
    from wgsextract_cli.commands.genotyping.hla import (
        PHARMCAT_GENES,
        parse_t1k,
        reported_diplotype,
    )

    evidence = source.output("hla_genotypes")
    try:
        calls = {c.gene: c for c in parse_t1k(str(evidence))} if evidence else None
    except (OSError, ValueError, IndexError) as e:
        section["warnings"].append(
            ReportWarningDocument(
                code="unreadable_evidence", message=f"T1K genotypes: {e}"
            )
        )
        calls = None
    release = source.metrics.get("ipd_imgt_hla_release")
    for gene in PHARMCAT_GENES:
        diplotype = source.metrics.get(gene)
        call = (calls or {}).get(gene)
        qualities = [q for _, q in call.alleles] if call else []
        if calls is not None and reported_diplotype(call) != diplotype:
            # e.g. a later, interrupted run replaced the genotypes
            label, reasons = NOT_ASSESSED, ["evidence_mismatch"]
            section["warnings"].append(
                ReportWarningDocument(
                    code="evidence_mismatch",
                    message=f"The T1K genotypes no longer give the {gene} call "
                    "typing hla recorded; rerun it.",
                )
            )
        elif not diplotype:
            label, reasons = variant_confidence.LOW, ["no_call"]
        elif calls is None:
            label, reasons = variant_confidence.MODERATE, ["quality_unknown"]
        else:
            label, reasons = variant_confidence.HIGH, []
        detail = "; ".join(
            x
            for x in (
                f"T1K quality {', '.join(map(str, qualities))}" if qualities else "",
                str(release or ""),
            )
            if x
        )
        section["findings"].append(
            _finding(
                "pharmacogenomics",
                f"{gene} alleles (T1K)",
                str(diplotype or "no confident call"),
                detail=detail or None,
                gene=gene,
                confidence=label,
                reasons=reasons,
                basis="T1K allele quality (above 0 for each allele, as T1K recommends)",
                evidence=ctx.rel(evidence),
                region=f"gene {gene}",
                manifest=ctx.rel(source.manifest),
            )
        )
