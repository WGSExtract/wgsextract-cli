"""Personal-report clinical support."""

from __future__ import annotations

import gzip
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from wgsextract_cli.core import (
    clinical_evidence,
    gene_lists,
    read_scope,
    variant_confidence,
)
from wgsextract_cli.core.builds import (
    build_from_chr1_length,
    canonical_build,
    is_hg37_build,
    is_hg38_build,
)
from wgsextract_cli.core.reference_models import sequence_lengths
from wgsextract_cli.core.report_documents import (
    ReportClinvarCountsDocument,
    ReportClinvarDocument,
    ReportClinvarFields,
    ReportGeneDocument,
    ReportVariantDocument,
    ReportWarningDocument,
)

from .context import (
    _VCF_SUFFIXES,
    CONFIDENCE,
    NOT_ASSESSED,
    SECTIONS,
    _command_or_role,
    _Context,
    _finding,
    _float,
    _int,
    _labelled_calls,
    _missing,
    _section,
)

# --- the gene list's build ---------------------------------------------------


def _gene_build(build: str | None) -> str | None:
    if not build:
        return None
    if is_hg38_build(build):
        return "hg38"
    if is_hg37_build(build):
        return "hg19"
    return None


def _vcf_build(path: Path) -> str | None:
    """The build a VCF's ##contig lines name, read locally from its header."""
    lines = []
    try:
        opener = gzip.open if path.name.endswith(("gz", "bgz")) else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not line.startswith("##"):
                    break
                lines.append(line)
    except (OSError, EOFError):
        return None
    return canonical_build(build_from_chr1_length(sequence_lengths("".join(lines))))


def _clinvar(ctx: _Context) -> ReportClinvarDocument:
    source = ctx.latest(_command_or_role("vcf clinvar", "clinvar_pathogenic"))
    common = _section("clinvar", [source], ctx)
    described = gene_lists.describe(ctx.gene_list)
    section: ReportClinvarDocument = {
        **common,
        **ReportClinvarFields(),
        "gene_list": described,
    }
    section["title"] = SECTIONS["clinvar"].format(gene_list=described["name"])
    if source is None or source.status != "ok":
        command = ctx.command_for("vcf clinvar", ctx.vcf, "vcf.gz")
        if ctx.vcf is None and ctx.alignment:
            command = (
                f"{ctx.command_for('vcf snp', ctx.alignment, 'cram')}, then {command}"
            )
        _missing(section, "ClinVar annotation (vcf clinvar)", command)
        return section
    vcf = source.output("clinvar_pathogenic")
    # The evidence VCF's own header, else the current facts of the VCF it read
    vcf_inputs = [
        source.locate(str(r["path"]))
        for role in ("vcf_input", "input")
        for r in source.records("inputs")
        if r.get("role") == role
        and str(r.get("path") or "").lower().endswith(_VCF_SUFFIXES)
        and read_scope.may_follow(source.locate(str(r["path"])))
    ]
    build = _gene_build(
        (_vcf_build(vcf) if vcf else None)
        or next(filter(None, map(ctx.build_of, vcf_inputs)), None)
    )
    labelled, complete = _labelled_calls(
        source, "clinvar_pathogenic", "confidence_table"
    )
    calls = [
        c
        for c in labelled
        if variant_confidence.is_pathogenic(str(c.get("CLNSIG") or ""))
    ]
    conflicts, _ = _labelled_calls(
        source, "clinvar_conflicts", "clinvar_conflict_table"
    )
    conflicts = [
        c for c in conflicts if clinical_evidence.review_evidence(c)["conflict"]
    ]
    gene_list = gene_lists.GeneList(described["id"], described["name"], ())
    unavailable = False
    if calls or conflicts:
        try:
            gene_list = gene_lists.load(ctx.gene_list)
        except gene_lists.GeneListUnavailable as error:
            unavailable = True
            section["warnings"].append(
                ReportWarningDocument(code="gene_list_unavailable", message=str(error))
            )
    if not unavailable:
        for call in calls:
            genes = _call_genes(
                gene_list,
                call,
                build,
                str(call.get("chrom") or ""),
                _int(call.get("pos")),
            )
            call["_report_gene_symbols"] = [g.symbol for g in genes] if genes else []
        calls = clinical_evidence.nonredundant_calls(calls)
    outside = unplaced = 0
    findings = []
    gene_calls: dict[str, tuple[gene_lists.Gene, list[Mapping[str, Any]]]] = {}
    for call in () if unavailable else [*calls, *conflicts]:
        review = clinical_evidence.review_evidence(call)
        pos = _int(call.get("pos"))
        chrom = str(call.get("chrom") or "")
        genes = _call_genes(gene_list, call, build, chrom, pos)
        if genes is None:
            unplaced += not review["conflict"]
            continue
        if not genes:
            outside += not review["conflict"]
            continue
        if "_report_gene_symbols" in call:
            genes = [g for g in genes if g.symbol in call["_report_gene_symbols"]]
        if not review["conflict"]:
            for gene in genes:
                gene_calls.setdefault(gene.symbol, (gene, []))[1].append(call)
        significance = str(call.get("CLNSIG") or "").replace("_", " ")
        # A variant ClinVar places in several listed genes is one finding.
        symbols = "/".join(g.symbol for g in genes)
        notes = [
            f"{g.symbol}: {g.note}" if len(genes) > 1 else g.note
            for g in genes
            if g.note
        ]
        if call.get("_mnv_residual_copies"):
            notes.append(
                f"{call['_mnv_residual_copies']} additional pathogenic copy outside the composed MNV; original genotype shown"
            )
        findings.append(
            _finding(
                "clinvar",
                f"{symbols} {significance}",
                significance,
                detail="; ".join(
                    filter(
                        None,
                        [
                            str(call.get("CLNDN") or "").replace("_", " "),
                            f"ClinVar review: {review['stars'] if review['stars'] is not None else 'unknown'} stars ({review['review_tier']})",
                            f"ClinVar ID: {review['id']}" if review["id"] else "",
                            f"Conflicting assertions (not consensus P/LP): {review['conflict_evidence'] or significance}"
                            if review["conflict"]
                            else "",
                        ],
                    )
                ),
                gene=symbols,
                note="; ".join(notes) or None,
                confidence=str(call.get("confidence") or NOT_ASSESSED),
                reasons=list(call.get("reasons") or []),
                basis=variant_confidence.SCHEME,
                evidence=ctx.rel(
                    source.output("clinvar_conflicts") if review["conflict"] else vcf
                ),
                region=f"{chrom}:{pos}",
                manifest=ctx.rel(source.manifest),
                variant=ReportVariantDocument(
                    chrom=chrom,
                    pos=pos,
                    ref=call.get("ref"),
                    alt=call.get("alt"),
                    genotype=call.get("genotype"),
                    zygosity=call.get("zygosity"),
                    depth=_int(call.get("depth")),
                    genotype_quality=_int(call.get("genotype_quality")),
                    alt_fraction=_float(call.get("alt_fraction")),
                    clinical_evidence=review,
                    original_record=call.get(clinical_evidence.ORIGIN) or None,
                    mnv_components=call.get(clinical_evidence.COMPONENTS) or None,
                    mnv_residual_copies=call.get("_mnv_residual_copies"),
                    phase_set=str(
                        call.get("_mnv_phase_set") or call.get("phase_set") or ""
                    )
                    or None,
                ),
            )
        )
    order = {label: i for i, label in enumerate(CONFIDENCE)}
    findings.sort(key=lambda f: (order.get(f["confidence"], 9), f["gene"] or ""))
    section["findings"] = findings
    section["genes"] = sorted(
        (_gene_calls(g, c, complete) for g, c in gene_calls.values()),
        key=lambda g: g["gene"],
    )
    if not complete:
        section["warnings"].append(
            ReportWarningDocument(
                code="calls_truncated",
                message="The run's confidence table is missing, and its "
                "manifest lists only some calls: gene counts may be incomplete, "
                "and only a biallelic status (which more calls can't change) is "
                "given. Rerun vcf clinvar.",
            )
        )
    section["build"] = build
    confidence_counts = {
        label: sum(
            f["confidence"] == label
            and not (
                f["variant"]
                and f["variant"].get("clinical_evidence", {}).get("conflict")
            )
            for f in findings
        )
        for label in variant_confidence.LABELS
    }
    section["counts"] = ReportClinvarCountsDocument(
        carried=len(calls),
        in_gene_list=sum(
            not (
                f["variant"]
                and f["variant"].get("clinical_evidence", {}).get("conflict")
            )
            for f in findings
        ),
        outside_gene_list=outside,
        unplaced=unplaced,
        high=confidence_counts["high"],
        moderate=confidence_counts["moderate"],
        low=confidence_counts["low"],
    )
    if conflicts:
        section["counts"]["conflicts_for_review"] = len(conflicts)
        section["all_conflicts"] = ctx.rel(
            source.output("clinvar_conflict_table")
            or source.output("clinvar_conflicts")
        )
    section["all_calls"] = ctx.rel(source.output("confidence_table") or vcf)
    if unavailable:
        section["counts"]["unplaced"] = len(calls)
        section["summary"] = (
            f"{len(calls)} call{'s' * (len(calls) != 1)} not placed in genes: "
            "the gene list is unavailable; see the full table."
        )
        return section
    if unplaced and build is None and gene_list.needs_build():
        section["warnings"].append(
            ReportWarningDocument(
                code="build_unknown",
                message="The VCF's build could not be identified, so its calls "
                "could not be placed in genes; see the full table.",
            )
        )
    elif unplaced:
        section["warnings"].append(
            ReportWarningDocument(
                code="gene_annotation_missing",
                message=f"{unplaced} call{'s' * (unplaced != 1)} have no ClinVar "
                "GENEINFO and the gene list has no spans to place them by; rerun "
                "vcf clinvar with a current ClinVar file, or use a list with "
                "spans. See the full table.",
            )
        )
    flagged = [
        f"{g['gene']} {g['status'].replace('_', ' ')}" + _qualifiers(g)
        for g in section["genes"]
        if g["status"]
    ]
    section["summary"] = (
        f"{section['counts']['in_gene_list']} call{'s' * (section['counts']['in_gene_list'] != 1)} in "
        f"{described['name']} genes; {outside} more elsewhere"
        + (f"; {unplaced} not placed" if unplaced else "")
        + (f". Recessive genes: {', '.join(flagged)}" if flagged else "")
        + (
            f"; {len(conflicts)} conflicting assertions for review (not consensus P/LP)"
            if conflicts
            else ""
        )
        + "."
    )
    return section


# A recessive gene's P/LP calls: one copy affected, (maybe) both.
GENE_STATUS = {
    "biallelic": "a homozygous P/LP call, or P/LP calls of two ALTs of one "
    "multi-allelic call (such as 1/2): both copies affected",
    "possible_biallelic": "possibly both copies affected: two or more "
    "heterozygous P/LP calls whose phase is unknown (unphased, or in different "
    "phase sets, so they may be on the same copy), or a call whose other "
    "allele was not called",
    "carrier": "one heterozygous P/LP call: one copy affected",
}
# The same statuses when the phase set (FORMAT/PS) of the calls decided them.
PHASED_STATUS = {
    "biallelic": "heterozygous P/LP calls phased on opposite copies (trans) "
    "in one phase set: both copies affected",
    "carrier": "heterozygous P/LP calls all phased on the same copy (cis) in "
    "one phase set: one copy affected",
}


def _split_index(origin: Sequence[str]) -> bool:
    """Whether ``origin`` (an ``--old-rec-tag`` split on ``|``) names one ALT
    of its record by number."""
    return (
        len(origin) == 5
        and origin[4].isdigit()
        and 1 <= int(origin[4]) <= len(origin[3].split(","))
    )


def _phased_copy(
    call: Mapping[str, Any], alleles: Sequence[str]
) -> tuple[tuple[str, str], int] | None:
    """The phase set (chromosome, FORMAT/PS) of a heterozygous diploid call
    phased within one, and the copy (0 or 1) carrying its ALT; None when its
    phase is unknown. Pipes without a PS don't say which calls were phased
    together, so they compare nothing."""
    genotype = str(call.get("_gene_genotype") or call.get("genotype") or "")
    phase_set = str(call.get("_mnv_phase_set") or call.get("phase_set") or "")
    if "/" in genotype or "|" not in genotype or phase_set in ("", "."):
        return None
    carried = [i for i, allele in enumerate(alleles) if allele != "0"]
    if len(alleles) != 2 or len(carried) != 1:
        return None
    return (str(call.get("chrom") or ""), phase_set), carried[0]


def _gene_calls(
    gene: gene_lists.Gene, calls: Sequence[Mapping[str, Any]], complete: bool = True
) -> ReportGeneDocument:
    """A listed gene's P/LP calls by zygosity, and for a gene the list reports
    only when biallelic (AR) whether this sample is a carrier or (possibly)
    biallelic, with the weakest confidence of those calls.

    Heterozygous calls are on known copies only when phased (``0|1``) in one
    phase set (FORMAT/PS): on one copy (cis) they leave the other unaffected,
    on opposite copies (trans) they affect both. So are two ALTs split from
    one diploid call (``1/2``). Calls of unknown phase, or in different phase
    sets, make the gene only possibly biallelic, as does a haploid or partly
    called genotype (``1/.``), which doesn't show the other copy."""
    het = hom = hemi = partial = unphased = 0
    split_alts: dict[str, set[str]] = {}
    phase_sets: dict[tuple[str, str], set[int]] = {}
    for call in calls:
        zygosity = str(call.get("zygosity") or "")
        gt = str(call.get("_gene_genotype") or call.get("genotype") or "")
        alleles = re.split(r"[/|]", gt)
        if call.get("_gene_genotype"):
            zygosity = "het" if alleles.count("1") == 1 else "hom"
        if zygosity == "het" and "." in alleles:
            partial += 1
        elif zygosity == "het":
            het += 1
            # CHR|POS|REF|ALTS|IDX of the record it was split from. Beside a
            # reference copy (0/1/2) the two ALTs leave one copy unaffected.
            origin = str(call.get(variant_confidence.SPLIT_TAG) or "").split("|")
            if len(alleles) == 2 and _split_index(origin):
                split_alts.setdefault("|".join(origin[:4]), set()).add(origin[4])
            if copy := _phased_copy(call, alleles):
                phase_sets.setdefault(copy[0], set()).add(copy[1])
            else:
                unphased += 1
        elif zygosity == "hom":
            hom += 1
        elif zygosity == "haploid":
            hemi += 1
    split_trans = any(len(alts) > 1 for alts in split_alts.values())
    phased_trans = any(len(copies) > 1 for copies in phase_sets.values())
    # Calls of one phase set on one copy are one allele; others may each be.
    alleles_seen = unphased + len(phase_sets)
    phase = None
    if het >= 2:
        if split_trans or phased_trans:
            phase = "trans"
        elif alleles_seen == 1:
            phase = "cis"
        else:
            phase = "unknown"
    status = None
    if gene.recessive:
        if hom or split_trans or phased_trans:
            status = "biallelic"
        elif hemi or partial or alleles_seen >= 2:
            status = "possible_biallelic"
        elif het:
            status = "carrier"
    if not complete and status != "biallelic":
        status = None
    if not complete and phase != "trans":
        phase = None
    # Phase sets decided it: no homozygous call or split 1/2 already did.
    by_phase = (status, phase) == ("carrier", "cis") or (
        status == "biallelic" and not hom and not split_trans
    )
    order = {label: i for i, label in enumerate(CONFIDENCE)}
    labels = [str(c.get("confidence") or NOT_ASSESSED) for c in calls]
    return ReportGeneDocument(
        gene=gene.symbol,
        inheritance=gene.inheritance or None,
        heterozygous=het,
        homozygous=hom,
        hemizygous=hemi,
        partial=partial,
        phase=phase,
        confidence=max(labels, key=lambda label: order.get(label, len(order))),
        status=status,
        status_text=(PHASED_STATUS if by_phase else GENE_STATUS).get(status or "", ""),
    )


def _qualifiers(gene: ReportGeneDocument) -> str:
    """' (cis; low-confidence calls)': the known phase behind the status, and
    the calls' confidence when not high."""
    known = (gene["status"], gene["phase"]) in {
        ("carrier", "cis"),
        ("biallelic", "trans"),
    }
    parts = [gene["phase"]] if known and gene["phase"] is not None else []
    if gene["confidence"] != "high":
        parts.append(_confidence_text(gene))
    return f" ({'; '.join(parts)})" if parts else ""


def _confidence_text(gene: ReportGeneDocument) -> str:
    if gene["confidence"] == NOT_ASSESSED:
        return "calls not assessed"
    return f"{gene['confidence']}-confidence calls"


def _call_genes(
    gene_list: gene_lists.GeneList,
    call: Mapping[str, Any],
    build: str | None,
    chrom: str,
    pos: int | None,
) -> list[gene_lists.Gene] | None:
    """The list's genes ClinVar names for the call (GENEINFO), or, for an
    annotation without GENEINFO, those whose span holds it; None if neither
    can place the call."""
    if symbols := _geneinfo(call):
        return gene_list.named(symbols)
    if pos is None or not gene_list.has_spans(build):
        return None
    return gene_list.genes_at(build, chrom, pos)


def _geneinfo(call: Mapping[str, Any]) -> list[str]:
    """The gene symbols of ClinVar's GENEINFO (``SYMBOL:GeneID|...``)."""
    text = str(call.get("GENEINFO") or "")
    return [e.split(":")[0] for e in text.split("|") if e and e != "."]
