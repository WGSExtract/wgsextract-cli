"""``report``: a sample's results as one local HTML, Markdown and JSON report.

The report is assembled from what earlier commands left in the sample folder:
their result manifests (``*.manifest.json``, docs/result-manifest.md) and the
sample workspace (``wgsextract.sample.toml``, docs/sample-workspace.md). It
runs no analysis. Its only network use is downloading a built-in gene list
(core/gene_lists.py) into the reference library the first time ClinVar calls
need one. A section whose command has not run says so and gives the command
that produces it.

Every finding carries a confidence label (``high``, ``moderate``, ``low``, or
``not_assessed`` when nothing supports a label), its reasons, the evidence
file (relative to the sample folder) with the region, and the "technical
result, not diagnosis" disclaimer. The JSON document follows
``assets/schemas/wgsextract.report.1.schema.json``; docs/personal-report.md
describes it.
"""

from __future__ import annotations

import csv as csv
import gzip as gzip
import json as json
import os as os
import re as re
import zlib as zlib
from collections.abc import Callable as Callable
from collections.abc import Iterable as Iterable
from collections.abc import Mapping as Mapping
from collections.abc import Sequence as Sequence
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from datetime import datetime as datetime
from datetime import timezone as timezone
from importlib import resources as resources
from pathlib import Path as Path
from pathlib import PureWindowsPath as PureWindowsPath
from typing import Any as Any
from typing import Final

from wgsextract_cli import __version__ as __version__
from wgsextract_cli.core import acmg as acmg
from wgsextract_cli.core import clinical_evidence as clinical_evidence
from wgsextract_cli.core import contamination as contamination
from wgsextract_cli.core import cpic_data as cpic_data
from wgsextract_cli.core import gene_lists as gene_lists
from wgsextract_cli.core import read_scope as read_scope
from wgsextract_cli.core import variant_confidence as variant_confidence
from wgsextract_cli.core.builds import build_from_chr1_length as build_from_chr1_length
from wgsextract_cli.core.builds import canonical_build as canonical_build
from wgsextract_cli.core.builds import is_hg37_build as is_hg37_build
from wgsextract_cli.core.builds import is_hg38_build as is_hg38_build
from wgsextract_cli.core.cpic_data import CpicData as CpicData
from wgsextract_cli.core.multiqc import find_documents as find_documents
from wgsextract_cli.core.pgx_context import PgxContext as PgxContext
from wgsextract_cli.core.ref_library.models import sequence_lengths as sequence_lengths
from wgsextract_cli.core.reference_assets import input_stem as input_stem
from wgsextract_cli.core.report_documents import (
    ReportAttemptDocument as ReportAttemptDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportDocument,
    ReportResearchSection,
    ReportSampleDocument,
    ReportSectionsDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportEvidenceDocument as ReportEvidenceDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportFactDocument as ReportFactDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportFindingDocument as ReportFindingDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportFindingSection as ReportFindingSection,
)
from wgsextract_cli.core.report_documents import (
    ReportLinkDocument as ReportLinkDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportMedicationReviewDocument as ReportMedicationReviewDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportMetricDocument as ReportMetricDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportMissingDocument as ReportMissingDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportPgxAlternativeDocument as ReportPgxAlternativeDocument,
)
from wgsextract_cli.core.report_documents import ReportPgxDocument as ReportPgxDocument
from wgsextract_cli.core.report_documents import (
    ReportSourceDocument as ReportSourceDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportVariantDocument as ReportVariantDocument,
)
from wgsextract_cli.core.report_documents import (
    ReportWarningDocument as ReportWarningDocument,
)
from wgsextract_cli.core.report_documents import section_items as section_items
from wgsextract_cli.core.sample_workspace import WORKSPACE_FILE as WORKSPACE_FILE
from wgsextract_cli.core.sample_workspace import SampleWorkspace as SampleWorkspace
from wgsextract_cli.core.table_writer import Column as Column
from wgsextract_cli.core.table_writer import Value as Value
from wgsextract_cli.core.utils import UsageError as UsageError

from . import acmg_calls as acmg_calls
from .clinical import (
    GENE_STATUS as GENE_STATUS,
)
from .clinical import (
    PHASED_STATUS as PHASED_STATUS,
)
from .clinical import (
    _call_genes as _call_genes,
)
from .clinical import (
    _clinvar as _clinvar,
)
from .clinical import (
    _confidence_text as _confidence_text,
)
from .clinical import (
    _gene_build as _gene_build,
)
from .clinical import (
    _gene_calls as _gene_calls,
)
from .clinical import (
    _geneinfo as _geneinfo,
)
from .clinical import (
    _phased_copy as _phased_copy,
)
from .clinical import (
    _qualifiers as _qualifiers,
)
from .clinical import (
    _split_index as _split_index,
)
from .clinical import (
    _vcf_build as _vcf_build,
)
from .context import (
    _ALIGNMENT_SUFFIXES as _ALIGNMENT_SUFFIXES,
)
from .context import (
    _ENDED as _ENDED,
)
from .context import (
    _VCF_SUFFIXES as _VCF_SUFFIXES,
)
from .context import (
    COMMAND as COMMAND,
)
from .context import (
    CONFIDENCE as CONFIDENCE,
)
from .context import (
    CONTAMINATION_LABEL as CONTAMINATION_LABEL,
)
from .context import (
    HIGH_QUALITY as HIGH_QUALITY,
)
from .context import (
    LOW_QUALITY as LOW_QUALITY,
)
from .context import (
    NOT_ASSESSED as NOT_ASSESSED,
)
from .context import (
    REASONS as REASONS,
)
from .context import (
    SAMPLE_ROLES as SAMPLE_ROLES,
)
from .context import (
    SECTIONS as SECTIONS,
)
from .context import (
    Source as Source,
)
from .context import (
    _choose_inputs as _choose_inputs,
)
from .context import (
    _command as _command,
)
from .context import (
    _command_or_role as _command_or_role,
)
from .context import (
    _contaminated as _contaminated,
)
from .context import (
    _contamination_fact as _contamination_fact,
)
from .context import (
    _Context as _Context,
)
from .context import (
    _fact_text as _fact_text,
)
from .context import (
    _files as _files,
)
from .context import (
    _finding as _finding,
)
from .context import (
    _float as _float,
)
from .context import (
    _inside as _inside,
)
from .context import (
    _int as _int,
)
from .context import (
    _key as _key,
)
from .context import (
    _labelled_calls as _labelled_calls,
)
from .context import (
    _missing as _missing,
)
from .context import (
    _quality_label as _quality_label,
)
from .context import (
    _quote as _quote,
)
from .context import (
    _roots as _roots,
)
from .context import (
    _section as _section,
)
from .context import (
    _worst as _worst,
)
from .context import (
    collect_sources as collect_sources,
)
from .context import (
    latest as latest,
)
from .context import (
    sample_groups as sample_groups,
)
from .context import (
    sample_sources as sample_sources,
)
from .context import (
    select_sample as select_sample,
)
from .genotyping import (
    _carrier_screening as _carrier_screening,
)
from .genotyping import (
    _cyrius_finding as _cyrius_finding,
)
from .genotyping import (
    _hla_findings as _hla_findings,
)
from .genotyping import (
    _pharmacogenomics as _pharmacogenomics,
)
from .genotyping import (
    _pharmcat_findings as _pharmcat_findings,
)
from .genotyping import (
    _smn_finding as _smn_finding,
)
from .medication_review import load_rules as load_rules
from .medication_review import medication_review as medication_review
from .overview import (
    _INFO_FACTS as _INFO_FACTS,
)
from .overview import (
    _MICROARRAY_ROLES as _MICROARRAY_ROLES,
)
from .overview import (
    _OVERVIEW_FACTS as _OVERVIEW_FACTS,
)
from .overview import (
    _QC_METRICS as _QC_METRICS,
)
from .overview import (
    ANCESTRY_NOTE as ANCESTRY_NOTE,
)
from .overview import (
    _ancestry as _ancestry,
)
from .overview import (
    _lineage as _lineage,
)
from .overview import (
    _microarray as _microarray,
)
from .overview import (
    _overview as _overview,
)
from .rendering import NEWER_ATTEMPT_FAILED as NEWER_ATTEMPT_FAILED
from .rendering import NOT_DIAGNOSIS as NOT_DIAGNOSIS
from .rendering import RESEARCH_ONLY as RESEARCH_ONLY
from .rendering import _number as _number
from .rendering import link_base as link_base
from .rendering import render_html as render_html
from .rendering import render_markdown as render_markdown
from .variant_sections import (
    _NOT_CARRIED as _NOT_CARRIED,
)
from .variant_sections import (
    MAX_SV_FINDINGS as MAX_SV_FINDINGS,
)
from .variant_sections import (
    _acmg_research as _acmg_research,
)
from .variant_sections import (
    _structural_variants as _structural_variants,
)

SCHEMA_ID: Final = "wgsextract.report/1"
SCHEMA_RESOURCE = "assets/schemas/wgsextract.report.1.schema.json"
GUIDE = "docs/personal-report.md"
DISCLAIMER = (
    "This report summarises technical results of software run on this "
    "computer; it is not a diagnosis or medical advice. "
    + variant_confidence.DISCLAIMER
)

# The findings table (``--format``): one row per finding, for agents and
# spreadsheets. The JSON document holds the same findings with their links.
TEXT, INTEGER, REAL = "TEXT", "INTEGER", "REAL"
FINDING_COLUMNS: tuple[Column, ...] = (
    Column(
        "section",
        TEXT,
        "Report section: lineage, pharmacogenomics, medication_review, "
        "carrier_screening, clinvar, acmg_research or structural_variants.",
    ),
    Column("label", TEXT, "What the finding is, e.g. 'BRCA2 Pathogenic'."),
    Column("value", TEXT, "The result: haplogroup, diplotype or ClinVar class."),
    Column("detail", TEXT, "Phenotype, condition or caller detail."),
    Column("gene", TEXT, "Gene symbol, when the finding is in a gene."),
    Column("chrom", TEXT, "Chromosome of a variant finding."),
    Column("pos", INTEGER, "1-based position of a variant finding."),
    Column("ref", TEXT, "Reference allele."),
    Column("alt", TEXT, "Alternate allele."),
    Column("genotype", TEXT, "The sample's GT."),
    Column("zygosity", TEXT, "het, hom_alt or hemizygous."),
    Column("depth", INTEGER, "Read depth at the call."),
    Column("genotype_quality", INTEGER, "Genotype quality (GQ)."),
    Column("alt_fraction", REAL, "Fraction of reads carrying the ALT."),
    Column(
        "confidence", TEXT, "Confidence label: high, moderate, low or not_assessed."
    ),
    Column("confidence_reasons", TEXT, "Reason codes, '|'-separated."),
    Column("evidence_file", TEXT, "Evidence file, relative to the sample folder."),
    Column("evidence_region", TEXT, "Region of the evidence (chrom:pos, gene)."),
    Column(
        "disclaimer",
        TEXT,
        "Technical result, not diagnosis (research use only for acmg_research; "
        "not a prescription for medication_review).",
    ),
    Column("clinvar_id", TEXT, "ClinVar resource record identity."),
    Column("clinvar_review_status", TEXT, "CLNREVSTAT assertion review status."),
    Column(
        "clinvar_review_stars",
        INTEGER,
        "ClinVar review stars (0-4), not sequencing confidence.",
    ),
    Column("clinvar_review_tier", TEXT, "ClinVar assertion support tier."),
    Column(
        "clinvar_conflict_evidence",
        TEXT,
        "CLNSIGCONF conflicting submitter classifications.",
    ),
    Column(
        "original_record", TEXT, "Original allele and sample fields (URL-encoded JSON)."
    ),
    Column(
        "mnv_components", TEXT, "Phase-supported MNV components (URL-encoded JSON)."
    ),
    Column(
        "mnv_residual_copies",
        INTEGER,
        "Pathogenic component copies outside a composed MNV; the original genotype is retained.",
    ),
)


_POSIX_PATH = re.compile(r"""(?<![\w.~:/-])/(?:[^\s/:'"<>|]+/)+([^\s/:'"<>|]*)""")
_WINDOWS_PATH = re.compile(
    r"""(?<![\w])[A-Za-z]:[\\/](?:[^\s\\/:'"<>|]+[\\/])*([^\s\\/:'"<>|]*)"""
)


def _path_scrubber(ctx: _Context) -> Callable[[str], str]:
    """Rewrites messages from the runs for sharing: paths in the sample folder
    (as it is now, or as a manifest recorded it before a move) relative to
    it, and any other absolute path as its file name alone."""
    prefixes: dict[str, str] = {}
    for source in ctx.sources:
        here = ctx.rel(source.manifest.parent) or "."
        for folder in (source.recorded_folder, str(source.manifest.parent)):
            if folder:
                prefixes[folder] = here
    prefixes[str(ctx.folder)] = "."
    variants = {
        spelling: rel
        for folder, rel in prefixes.items()
        for spelling in {folder, folder.replace("\\", "/")}
        if Path(spelling).is_absolute() or PureWindowsPath(spelling).is_absolute()
    }
    ordered = sorted(variants.items(), key=lambda item: -len(item[0]))

    def scrub(text: str) -> str:
        for folder, rel in ordered:
            lead = "" if rel == "." else f"{rel}/"
            for sep in ("/", "\\"):
                text = text.replace(folder + sep, lead)
        text = _WINDOWS_PATH.sub(lambda m: m.group(1) or "…", text)
        return _POSIX_PATH.sub(lambda m: m.group(1) or "…", text)

    return scrub


def build_report(
    folder: Path,
    *,
    input_file: Path | None = None,
    results: Iterable[Path] = (),
    sources: list[Source] | None = None,
    gene_list: str | Path | None = None,
    pgx_context: PgxContext | None = None,
    cpic: CpicData | None = None,
) -> ReportDocument:
    """The report document (``wgsextract.report/1``) of the sample in ``folder``.

    ``gene_list`` is a built-in list's id or a TSV or BED file (core/gene_lists.py);
    by default the newest ACMG SF list. ``pgx_context`` (the person's listed
    medications) adds the medication review, from ``cpic`` (default: the
    pinned CPIC release in the reference library, which may need --download)."""
    gene_lists.describe(gene_list)
    folder = Path(os.path.abspath(folder))
    if sources is None:
        sources = sample_sources(folder, input_file, results)
    workspace = SampleWorkspace(folder)
    alignment, vcf = _choose_inputs(folder, input_file, sources, workspace)
    main_file = alignment or vcf
    described = workspace.describe(folder / main_file if main_file else None)
    ctx = _Context(folder, sources, dict(described), alignment, vcf, gene_list)
    info = latest(sources, _command("info"))
    name = (
        input_stem(alignment or vcf or "")
        or (
            input_stem(info.files("inputs")[0]) if info and info.files("inputs") else ""
        )
        or folder.name
    )
    research = _acmg_research(ctx)
    research_section = ReportResearchSection()
    # Opt-in research: shown only when `vcf acmg` ran, never asked for.
    if research is not None:
        research_section["acmg_research"] = research
    pharmacogenomics = _pharmacogenomics(ctx)
    review: dict[str, ReportMedicationReviewDocument] = {}
    # Opt-in: only for medications the person lists (--pgx-context).
    if pgx_context is not None:
        review["medication_review"] = medication_review(
            ctx, pgx_context, cpic or cpic_data.load(), load_rules()
        )
    sections = ReportSectionsDocument(
        overview=_overview(ctx),
        lineage=_lineage(ctx),
        ancestry=_ancestry(ctx),
        pharmacogenomics=pharmacogenomics,
        **review,  # type: ignore[typeddict-item]
        carrier_screening=_carrier_screening(ctx),
        clinvar=_clinvar(ctx),
        **research_section,
        structural_variants=_structural_variants(ctx),
        microarray=_microarray(ctx),
    )
    scrub = _path_scrubber(ctx)
    for _, section in section_items(sections):
        for warning in section["warnings"]:
            warning["message"] = scrub(warning["message"])
        if error := section.get("error"):
            section["error"] = scrub(error)
        for finding in section["findings"]:
            for file in (finding["evidence"]["file"], finding["evidence"]["manifest"]):
                if file and not (folder / file).exists():
                    section["warnings"].append(
                        ReportWarningDocument(
                            code="evidence_missing",
                            message=f"Evidence file not found: {file}",
                        )
                    )
    return ReportDocument(
        schema=SCHEMA_ID,
        generated=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        wgsextract_version=__version__,
        sample=ReportSampleDocument(
            name=name,
            folder=folder.name,
            alignment=alignment,
            vcf=vcf,
            workspace=WORKSPACE_FILE if described.get("exists") else None,
        ),
        disclaimer=DISCLAIMER,
        guide=GUIDE,
        confidence_reasons=REASONS,
        sections=sections,
    )


def findings(report: ReportDocument) -> list[ReportFindingDocument]:
    return [
        f
        for _, section in section_items(report["sections"])
        for f in section["findings"]
    ]


def table_rows(report: ReportDocument) -> list[list[Value]]:
    rows: list[list[Value]] = []
    for f in findings(report):
        v = f["variant"]
        clinical = v.get("clinical_evidence") if v else None
        rows.append(
            [
                f["section"],
                f["label"],
                f["value"],
                f["detail"],
                f["gene"],
                v.get("chrom") if v else None,
                v.get("pos") if v else None,
                v.get("ref") if v else None,
                v.get("alt") if v else None,
                v.get("genotype") if v else None,
                v.get("zygosity") if v else None,
                v.get("depth") if v else None,
                v.get("genotype_quality") if v else None,
                v.get("alt_fraction") if v else None,
                f["confidence"],
                "|".join(f["confidence_reasons"]),
                f["evidence"]["file"],
                f["evidence"]["region"],
                f["disclaimer"],
                clinical.get("id") if clinical else None,
                clinical.get("review_status") if clinical else None,
                clinical.get("stars") if clinical else None,
                clinical.get("review_tier") if clinical else None,
                clinical.get("conflict_evidence") if clinical else None,
                v.get("original_record") if v else None,
                v.get("mnv_components") if v else None,
                v.get("mnv_residual_copies") if v else None,
            ]
        )
    return rows


def load_schema() -> dict[str, Any]:
    """The wgsextract.report/1 JSON Schema shipped with the package."""
    text = resources.files("wgsextract_cli").joinpath(SCHEMA_RESOURCE).read_text()
    schema: dict[str, Any] = json.loads(text)
    return schema
