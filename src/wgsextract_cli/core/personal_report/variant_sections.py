"""Personal-report variant sections support."""

from __future__ import annotations

import csv
import json
import zlib
from collections.abc import Mapping
from typing import Any

from wgsextract_cli.core import (
    acmg,
    gene_lists,
    variant_confidence,
)
from wgsextract_cli.core.report_documents import (
    ReportAcmgCountsDocument,
    ReportAcmgDocument,
    ReportAcmgFields,
    ReportSvDocument,
    ReportSvFields,
    ReportSvThresholdsDocument,
    ReportVariantDocument,
    ReportWarningDocument,
)

from . import acmg_calls
from .clinical import (
    _gene_build,
    _vcf_build,
)
from .context import (
    CONFIDENCE,
    NOT_ASSESSED,
    SECTIONS,
    _command_or_role,
    _contaminated,
    _Context,
    _finding,
    _float,
    _int,
    _missing,
    _section,
)
from .rendering import (
    RESEARCH_ONLY,
    _number,
)


def _acmg_research(ctx: _Context) -> ReportAcmgDocument | None:
    """fastVEP's automated ACMG/AMP classes (`vcf acmg`, issue 629): the
    carried alleles' classes, and the P/LP calls in the gene list's genes
    under its reporting rules. Research only; None when `vcf acmg` never ran."""
    source = ctx.latest(_command_or_role("vcf acmg", "acmg_summary"))
    if source is None:
        return None
    common = _section("acmg_research", [source], ctx)
    described = gene_lists.describe(ctx.gene_list)
    section: ReportAcmgDocument = {
        **common,
        **ReportAcmgFields(),
        "gene_list": described,
        "disclaimer": acmg.DISCLAIMER,
    }
    section["title"] = SECTIONS["acmg_research"].format(gene_list=described["name"])
    # The disclaimer heads the section; the run's warning would repeat it.
    section["warnings"] = [
        w for w in section["warnings"] if w["code"] != "acmg_research_only"
    ]
    if source.status != "ok":
        return section
    summary: Mapping[str, Any] = {}
    if path := source.output("acmg_summary"):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = None
        summary = loaded if isinstance(loaded, Mapping) else {}
    tool = summary.get("tool") or {}
    section["tool"] = (
        f"{tool.get('name', 'fastVEP')} {tool['version']}"
        if tool.get("version")
        else None
    )
    section["criteria_not_evaluated"] = [
        str(c) for c in summary.get("criteria_not_evaluated") or []
    ]
    vcf = source.output("annotated_vcf")
    section["all_calls"] = ctx.rel(vcf)
    if vcf is None:
        section["warnings"].append(
            {
                "code": "evidence_missing",
                "message": "The classified VCF of the vcf acmg run is missing; "
                "rerun vcf acmg.",
            }
        )
        section["summary"] = "The classified VCF is missing."
        return section
    try:
        found = acmg_calls.scan(
            vcf,
            variant_confidence.thresholds_from(),
            contaminated=_contaminated(ctx, source.files("inputs")) is not None,
        )
    except (OSError, EOFError, ValueError, IndexError, zlib.error) as e:
        section["warnings"].append(
            ReportWarningDocument(
                code="unreadable_evidence",
                message=f"The classified VCF {vcf.name} can't be read ({e}); "
                "rerun vcf acmg.",
            )
        )
        section["summary"] = "The classified VCF can't be read."
        return section
    samples = found.header.samples
    if len(samples) > 1:
        section["warnings"].append(
            ReportWarningDocument(
                code="confidence_first_sample_only",
                message=f"The classes counted and the calls listed are the first "
                f"sample's ({samples[0]}) of this {len(samples)}-sample VCF; "
                "subset it to the intended sample (bcftools view -s) and rerun "
                "vcf acmg.",
            )
        )
    if found.rare_imputed:
        section["warnings"].append(
            ReportWarningDocument(
                code="rare_imputed_not_listed",
                message=f"{found.rare_imputed} imputed P/LP "
                f"allele{'s' * (found.rare_imputed != 1)} rarer than 1% in the "
                "panel (or of unknown panel frequency) are counted but not "
                "listed: imputation can't see a rare allele the reads don't "
                "show. See the classified VCF.",
            )
        )
    build = _gene_build(_vcf_build(vcf))
    section["build"] = build
    gene_list = gene_lists.GeneList(described["id"], described["name"], ())
    unavailable = False
    if found.calls:
        try:
            gene_list = gene_lists.load(ctx.gene_list)
        except gene_lists.GeneListUnavailable as error:
            unavailable = True
            section["warnings"].append(
                {"code": "gene_list_unavailable", "message": str(error)}
            )
    findings = []
    outside = unplaced = 0
    for call in () if unavailable else found.calls:
        row = call.row
        chrom, pos = str(row["chrom"]), _int(row["pos"])
        symbol = str(row["gene"] or "")
        if symbol:
            genes = gene_list.named([symbol])
        elif pos is not None and gene_list.has_spans(build):
            genes = gene_list.genes_at(build, chrom, pos)
        else:
            unplaced += 1
            continue
        if not genes:
            outside += 1
            continue
        cls = str(row["acmg_class"])
        symbols = "/".join(g.symbol for g in genes)
        criteria = str(row["acmg_criteria"] or "").replace("&", ", ")
        findings.append(
            _finding(
                "acmg_research",
                f"{symbols} {cls} (automated)",
                f"{acmg.CLASSES[cls]} (automated)",
                detail="; ".join(
                    filter(
                        None,
                        [
                            f"criteria met: {criteria}" if criteria else "",
                            f"classified on {row['acmg_transcript']}"
                            if row["acmg_transcript"]
                            else "",
                            str(row["hgvs_p"] or row["hgvs_c"] or ""),
                            str(row["consequence"] or ""),
                        ],
                    )
                ),
                gene=symbols,
                note="; ".join(
                    f"{g.symbol}: {g.note}" if len(genes) > 1 else g.note
                    for g in genes
                    if g.note
                )
                or None,
                confidence=call.confidence,
                reasons=call.reasons,
                basis="not assessed: a sites-only VCF has no genotype to judge"
                if found.sites_only
                else variant_confidence.SCHEME,
                evidence=ctx.rel(vcf),
                region=f"{chrom}:{pos}",
                manifest=ctx.rel(source.manifest),
                variant=ReportVariantDocument(
                    chrom=chrom,
                    pos=pos,
                    ref=row["ref"],
                    alt=row["alt"],
                    genotype=row["genotype"],
                    zygosity=row["zygosity"],
                    depth=row["depth"],
                    genotype_quality=row["gq"],
                    alt_fraction=call.alt_fraction,
                    phase_set=call.phase_set,
                ),
                disclaimer=RESEARCH_ONLY,
            )
        )
    order = {label: i for i, label in enumerate(CONFIDENCE)}
    findings.sort(key=lambda f: (order.get(f["confidence"], 9), f["gene"] or ""))
    section["findings"] = findings
    pathogenic = len(found.calls)
    if unavailable:
        unplaced = pathogenic
    section["counts"] = ReportAcmgCountsDocument(
        p=found.carried["P"],
        lp=found.carried["LP"],
        vus=found.carried["VUS"],
        lb=found.carried["LB"],
        b=found.carried["B"],
        in_gene_list=len(findings),
        outside_gene_list=outside,
        unplaced=unplaced,
    )
    if unplaced and not unavailable:
        section["warnings"].append(
            {
                "code": "gene_annotation_missing",
                "message": f"{unplaced} P/LP call{'s' * (unplaced != 1)} have no "
                "VEP gene symbol and the gene list has no spans to place them by; "
                "see the classified VCF.",
            }
        )
    classes = ", ".join(f"{found.carried[c]:,} {c}" for c in acmg.CLASSES)
    alleles = "alleles" if found.sites_only else "carried alleles"
    section["summary"] = (
        f"fastVEP's automated classes of the {sum(found.carried.values()):,} "
        f"{alleles} it classified: {classes}. "
        f"{len(findings):,} P/LP call{'s' * (len(findings) != 1)} in "
        f"{described['name']} genes; {outside:,} more elsewhere"
        + (f"; {unplaced:,} not placed" if unplaced else "")
        + ". Research use only; not a diagnosis."
    )
    return section


# Rare SVs listed as findings, rarest first; the rest are in the table.
MAX_SV_FINDINGS = 100
_NOT_CARRIED = {"0/0", "0|0", "./.", ".|.", ".", "0"}


def _structural_variants(ctx: _Context) -> ReportSvDocument:
    source = ctx.latest(_command_or_role("vcf sv-freq", "sv_frequencies"))
    section: ReportSvDocument = {
        **_section("structural_variants", [source], ctx),
        **ReportSvFields(),
    }
    if source is None or source.status != "ok":
        command = ctx.command_for("vcf sv-freq", "sv.vcf.gz", "vcf.gz")
        if ctx.alignment:
            command = (
                f"{ctx.command_for('vcf sv', ctx.alignment, 'cram')}, then {command}"
            )
        _missing(section, "SV population frequencies (vcf sv-freq)", command)
        return section
    table = source.output("sv_frequencies")
    rows: list[dict[str, str]] = []
    if table is not None:
        try:
            with table.open(encoding="utf-8", newline="") as stream:
                lines = (line for line in stream if not line.startswith("#"))
                rows = list(csv.DictReader(lines, delimiter="\t"))
        except (OSError, csv.Error):
            rows = []
    common = _float(source.metrics.get("common_af")) or 0.01
    rare = _float(source.metrics.get("rare_af")) or 0.001
    carried = [
        r
        for r in rows
        if (r.get("genotype") or "") not in _NOT_CARRIED
        and (r.get("filter") or "PASS") in ("PASS", ".")
    ]
    counts = {
        label: sum(r.get("sv_freq") == label for r in carried)
        for label in ("common", "low", "rare", "novel")
    }
    rare_rows = sorted(
        (r for r in carried if r.get("sv_freq") == "rare"),
        key=lambda r: _float(r.get("sv_af")) or 0.0,
    )
    findings = []
    for r in rare_rows[:MAX_SV_FINDINGS]:
        chrom = str(r.get("chrom") or "")
        pos, end = _int(r.get("pos")), _int(r.get("end"))
        af = _float(r.get("sv_af"))
        size = f", {_number(_int(r.get('svlen')))} bp" if r.get("svlen") else ""
        region = f"{r.get('chrom')}:{pos}" + (f"-{end}" if end and end != pos else "")
        findings.append(
            _finding(
                "structural_variants",
                f"{r.get('svtype')} {region}{size}",
                f"rare (AF < {rare:g})",
                detail=f"gnomAD AF {af:.2g} ({r.get('sv_af_source')} "
                f"{r.get('sv_af_id')}; match {r.get('sv_af_match')})"
                if af is not None
                else None,
                confidence=NOT_ASSESSED,
                reasons=[],
                basis="not assessed: frequency is from the gnomAD match, not "
                "the call's read support",
                evidence=ctx.rel(source.output("annotated_vcf")),
                region=region,
                manifest=ctx.rel(source.manifest),
                variant=ReportVariantDocument(
                    chrom=chrom,
                    pos=pos,
                    ref=None,
                    alt=f"<{r.get('svtype')}>",
                    genotype=r.get("genotype"),
                    zygosity=None,
                    depth=None,
                    genotype_quality=None,
                    alt_fraction=None,
                ),
            )
        )
    section["findings"] = findings
    section["all_svs"] = ctx.rel(table)
    section["thresholds"] = ReportSvThresholdsDocument(common_af=common, rare_af=rare)
    section["counts"] = {**counts, "listed": len(findings)}
    if len(rare_rows) > len(findings):
        section["warnings"].append(
            ReportWarningDocument(
                code="svs_truncated",
                message=f"Only the {len(findings)} rarest of {len(rare_rows)} "
                "rare SVs are listed; see the full table.",
            )
        )
    section["summary"] = (
        f"{sum(counts.values())} carried PASS SVs: {counts['common']} common "
        f"(AF >= {common:g}), {counts['low']} low-frequency, {counts['rare']} rare "
        f"(AF < {rare:g}) and {counts['novel']} with no eligible same-type match. "
        "Nondirectional CNVs are unassessed, not evidence of absence from gnomAD. "
        "Other unmatched calls may reflect catalogue gaps or caller artefacts, "
        "not rarity. Common SVs are unlikely to cause rare disease; "
        "rare matched SVs may warrant investigation."
    )
    return section
