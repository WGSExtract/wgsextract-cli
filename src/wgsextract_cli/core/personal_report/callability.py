"""Personal-report exon callability: what the ClinVar section's silence means.

Reads the newest ``vcf callability`` run (its ``exon_callability`` JSON) and
gives each gene its technical state and a result: reported, not reported (every
coding exon read, nothing found; never a negative result), not readable or not
run. See docs/exon-callability.md.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from wgsextract_cli.core import exon_callability as ec
from wgsextract_cli.core import gene_lists, read_scope
from wgsextract_cli.core import reference_evidence as evidence
from wgsextract_cli.core.report_documents import (
    ReportCallabilityDocument,
    ReportCallabilityFields,
    ReportCallabilityGeneDocument,
    ReportClinvarDocument,
    ReportGeneListDocument,
    ReportWarningDocument,
)
from wgsextract_cli.core.results import fingerprint

from .context import (
    CONFIDENCE,
    SAMPLE_ROLES,
    SECTIONS,
    Source,
    _command_or_role,
    _Context,
    _missing,
    _section,
)

COMMAND = "vcf callability"
ROLE = "exon_callability"
RESULTS = ("reported", "not_readable", "not_run", "not_reported")
# ClinVar section warnings that leave its findings incomplete.
_INCOMPLETE = frozenset({"gene_list_unavailable", "calls_truncated"})


def _symbols(spec: str | Path | None) -> set[str] | None:
    """The ClinVar section's gene list symbols (upper case); None when they
    can't be read offline."""
    list_id = str(spec or gene_lists.default_id())
    try:
        if list_id in gene_lists.builtin_ids():
            return {s.upper() for s in gene_lists.builtin_symbols(list_id)}
        return {g.symbol.upper() for g in gene_lists.load(list_id).genes}
    except Exception:  # noqa: BLE001 - an unreadable list covers no gene
        return None


def _reported(clinvar: ReportClinvarDocument) -> dict[str, str]:
    """Genes with a consensus P/LP finding (conflicting assertions are for
    review, not reported), with the weakest confidence of their calls."""
    order = {label: i for i, label in enumerate(CONFIDENCE)}
    weakest: dict[str, str] = {}
    for f in clinvar["findings"]:
        if f["variant"] and f["variant"].get("clinical_evidence", {}).get("conflict"):
            continue
        for symbol in filter(None, (f["gene"] or "").upper().split("/")):
            old = weakest.get(symbol, f["confidence"])
            weakest[symbol] = max(old, f["confidence"], key=lambda c: order.get(c, 9))
    return weakest


def _not_callable(gene: Mapping[str, Any]) -> str:
    return "; ".join(
        f"exon{'s' * (len(numbers) > 1)} {ec.ranges(numbers)} "
        + status.replace("_", " ")
        for status in ec.NOT_CALLABLE_ORDER
        if (numbers := gene["not_callable"].get(status))
    )


def _result(
    gene: Mapping[str, Any],
    clinvar: ReportClinvarDocument,
    reported: Mapping[str, str],
    listed: set[str],
    blocked: str | None,
) -> tuple[str, str]:
    symbol = str(gene["gene"]).upper()
    if symbol in reported:
        label = reported[symbol].replace("_", " ")
        return "reported", f"ClinVar P/LP call ({label} confidence; see ClinVar)"
    if clinvar["status"] != "ok":
        return "not_run", "ClinVar annotation (vcf clinvar) has not run"
    if blocked:
        return "not_run", blocked
    if symbol not in listed:
        return "not_run", "not in the ClinVar section's gene list"
    if gene["state"] == ec.NOT_RUN:
        why = (
            "no exon model for this gene"
            if gene["reasons"] == ["no_exon_model"]
            else "the caller did not assess its coding exons"
        )
        return "not_run", why
    if gene["state"] == ec.CALLABLE:
        return "not_reported", "no P/LP call where every coding exon was read"
    return "not_readable", "no P/LP call, but not every coding exon was read"


def _recorded(source: Source, roles: Sequence[str]) -> dict[Path, str | None]:
    """The run's inputs with ``roles``, where they are now, and the
    fingerprints the run recorded for them."""
    return {
        source.locate(str(r["path"])).resolve(): r.get("fingerprint")
        for r in source.records("inputs")
        if r.get("role") in roles and r.get("path")
    }


def _same_calls(ctx: _Context, source: Source) -> bool:
    """Whether the ClinVar run read the very calls (same content) behind the
    gVCF assessed: the same VCF, or the VCF whose gVCF was assessed. False
    when that can't be established, as when either file has been rewritten
    since one of the runs."""
    clinvar = ctx.latest(_command_or_role("vcf clinvar", "clinvar_pathogenic"))
    if clinvar is None or clinvar.status != "ok":
        return True
    ours = _recorded(source, (*SAMPLE_ROLES, evidence.ROLE))
    for vcf, recorded in _recorded(clinvar, SAMPLE_ROLES).items():
        if recorded is None:
            continue
        if ours.get(vcf) == recorded:
            return True
        # Its gVCF, if the VCF is still the one ClinVar read.
        if not read_scope.may_follow(vcf) or fingerprint(vcf) != recorded:
            continue
        gvcf = evidence.companion(vcf)
        if gvcf is not None:
            assessed = ours.get(Path(gvcf).resolve())
            if assessed is not None and assessed == fingerprint(gvcf):
                return True
    return False


def _blocked(
    ctx: _Context,
    source: Source,
    document: Mapping[str, Any],
    clinvar: ReportClinvarDocument,
) -> str | None:
    """Why the ClinVar section's silence can't be read gene by gene, if it
    can't; each reason is also a section warning."""
    build, theirs = document.get("build"), clinvar.get("build")
    if build and theirs and build != theirs:
        return f"the exon assessment is on {build} but the ClinVar run on {theirs}"
    if clinvar["status"] != "ok":
        return None
    if not _same_calls(ctx, source):
        return "the ClinVar run annotated other variant calls than the assessed gVCF's"
    codes = {w["code"] for w in clinvar["warnings"]}
    if clinvar.get("counts", {}).get("unplaced") or codes & _INCOMPLETE:
        return (
            "some ClinVar calls could not be placed in genes (see the ClinVar section)"
        )
    return None


def _exon_callability(
    ctx: _Context, clinvar: ReportClinvarDocument
) -> ReportCallabilityDocument:
    source = ctx.latest(_command_or_role(COMMAND, ROLE))
    section: ReportCallabilityDocument = {
        **_section("exon_callability", [source], ctx),
        **ReportCallabilityFields(),
        "note": ec.NOTE,
    }
    section["title"] = SECTIONS["exon_callability"].format(
        gene_list=clinvar["gene_list"]["name"]
    )
    if source is None or source.status != "ok":
        vcf = ctx.folder / ctx.vcf if ctx.vcf else None
        if ctx.alignment and (vcf is None or evidence.companion(vcf) is None):
            _missing(
                section,
                "Reference evidence (a gVCF beside the variant calls)",
                f"{ctx.command_for('vcf snp', ctx.alignment, 'cram')} "
                "--reference-evidence gvcf",
            )
        _missing(
            section,
            "Exon callability (vcf callability)",
            ctx.command_for(COMMAND, ctx.vcf, "vcf.gz"),
        )
        return section
    path = source.output(ROLE)
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        document = None
    if not isinstance(document, dict) or document.get("schema") != ec.SCHEMA:
        section["warnings"].append(
            ReportWarningDocument(
                code="callability_unreadable",
                message=f"The exon callability document ({ctx.rel(path)}) is "
                "missing or unreadable; rerun vcf callability.",
            )
        )
        return section
    described = ReportGeneListDocument(**document["gene_list"])  # type: ignore[typeddict-item]
    section["title"] = SECTIONS["exon_callability"].format(gene_list=described["name"])
    build = document.get("build")
    # The gVCF, recorded relative to the document.
    gvcf = path.parent / str(document["evidence"]["file"]) if path else Path()
    blocked = _blocked(ctx, source, document, clinvar)
    if blocked:
        section["warnings"].append(
            ReportWarningDocument(
                code="clinvar_not_combined",
                message=f"Unreported genes are not run: {blocked}.",
            )
        )
    reported = _reported(clinvar)
    # The report's own list, by its symbols: two users' lists may share a name.
    listed = _symbols(ctx.gene_list) or set()
    genes: list[ReportCallabilityGeneDocument] = []
    for gene in document["genes"]:
        result, text = _result(gene, clinvar, reported, listed, blocked)
        genes.append(
            ReportCallabilityGeneDocument(
                gene=gene["gene"],
                transcript=gene["transcript"],
                state=gene["state"],
                result=result,
                result_text=text,
                coding_exons=gene["coding_exons"],
                exons_callable=gene["exons"][ec.CALLABLE],
                exons_not_callable=_not_callable(gene),
                bases=gene["bases"],
                readable_bases=gene["readable_bases"],
                reasons=list(gene["reasons"]),
            )
        )
    order = {name: i for i, name in enumerate(RESULTS)}
    genes.sort(key=lambda g: (order[g["result"]], g["gene"]))
    model = document["exon_model"]
    thresholds = document["thresholds"]
    section["gene_list"] = described
    section["build"] = build
    section["exon_model"] = f"{model['name']}: {model['transcripts']} transcripts" + (
        f"; {str(model['source'][0]).rstrip('.')}" if model.get("source") else ""
    )
    section["scope"] = str(thresholds.get("scope") or "")
    section["thresholds"] = thresholds
    section["genes"] = genes
    section["all_exons"] = ctx.rel(source.output("exon_callability_table"))
    section["evidence"] = ctx.rel(gvcf)
    section["unassessed_variant_classes"] = dict(document["unassessed_variant_classes"])
    states = {s: sum(g["state"] == s for g in genes) for s in ec.GENE_STATES}
    results = {r: sum(g["result"] == r for g in genes) for r in RESULTS}
    section["counts"] = {
        "genes": len(genes),
        **states,
        **{f"result_{r}": n for r, n in results.items()},
    }
    section["summary"] = (
        f"{len(genes)} genes ({section['scope']}, {build or 'build unknown'}): "
        + ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in states.items())
        + ". Results: "
        + ", ".join(f"{n} {r.replace('_', ' ')}" for r, n in results.items())
        + "."
    )
    return section
