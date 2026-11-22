"""Typed report components used at both construction and presentation boundaries."""

from collections.abc import ItemsView, Mapping
from typing import Annotated, Literal, TypedDict, cast

from wgsextract_cli.core.clinical_evidence import ClinicalEvidenceDocument
from wgsextract_cli.core.output_schema import Constraints


class ReportAttemptDocument(TypedDict):
    command: str
    status: str
    finished: str | None
    manifest: str | None


class ReportSourceDocument(ReportAttemptDocument, total=False):
    latest_attempt: Annotated[
        ReportAttemptDocument | None,
        Constraints(
            description="The sample's newest run of the same step when it is newer than this one, so it failed or was cancelled (the section also has a newer_attempt_failed warning); null (or absent, in reports written before it was added) when this run is the newest."
        ),
    ]


class ReportWarningDocument(TypedDict):
    code: str
    message: str


class ReportMissingDocument(TypedDict):
    what: str
    command: str


class ReportLinkDocument(TypedDict):
    label: str
    url: str


class ReportEvidenceDocument(TypedDict):
    file: Annotated[
        str | None,
        Constraints(description="Evidence file, relative to the sample folder."),
    ]
    region: Annotated[
        str | None,
        Constraints(description="chrom:pos, a chromosome or 'gene SYMBOL'."),
    ]
    manifest: Annotated[
        str | None,
        Constraints(
            description="The run's result manifest, relative to the sample folder."
        ),
    ]


class ReportVariantRequired(TypedDict):
    chrom: str
    pos: int | None
    ref: str | None
    alt: str | None
    genotype: str | None
    zygosity: str | None
    depth: int | None
    genotype_quality: int | None
    alt_fraction: float | None


class ReportVariantDocument(ReportVariantRequired, total=False):
    clinical_evidence: ClinicalEvidenceDocument
    original_record: str | None
    mnv_components: str | None
    mnv_residual_copies: Annotated[int | None, Constraints(minimum=1, maximum=2)]
    phase_set: Annotated[
        str | None,
        Constraints(
            description="FORMAT/PS of a ClinVar call: calls phased (0|1) in one phase set are on known copies."
        ),
    ]


class ReportPgxAlternativeDocument(TypedDict):
    diplotype: str
    phenotype: str


class ReportPgxDocument(TypedDict):
    status: Annotated[str, Constraints(enum=("resolved", "ambiguous", "no_call"))]
    alternatives: list[ReportPgxAlternativeDocument]
    phenotype_consistent: bool
    source: Literal["PharmCAT"]


class ReportCpicRecommendationDocument(TypedDict):
    id: str
    text: Annotated[str, Constraints(description="CPIC's recommendation, verbatim.")]
    classification: Annotated[
        str, Constraints(description="CPIC's strength: Strong, Moderate, Optional, ...")
    ]
    phenotypes: Annotated[
        dict[str, str], Constraints(description="Gene -> the phenotype it is for.")
    ]
    implication: str
    population: str
    guideline: str
    pmid: str | None
    doi: str | None
    url: str


class ReportAdvisoryDocument(TypedDict):
    status: Annotated[
        str,
        Constraints(
            enum=(
                "advisory",
                "context_review",
                "withheld",
                "context_needed",
                "insufficient_evidence",
                "not_assessed",
            ),
            description="advisory: one CPIC recommendation fits the confident call; context_review: it fits the genotype but a listed medication may change the phenotype or interact; withheld: the call's alternatives (or a weak call's possible results) lead to different recommendations; context_needed: CPIC's guidance differs by patient group or indication (give population); insufficient_evidence: CPIC has no recommendation for this medication, result or population; not_assessed: a gene the guidance needs has no result.",
        ),
    ]
    medication: str
    cpic_drug: str | None
    population: str | None
    genes: list[str]
    contraindication: Annotated[
        bool,
        Constraints(
            description="CPIC's recommendation advises against the drug (avoid, not recommended, contraindicated, do not use)."
        ),
    ]
    recommendation: ReportCpicRecommendationDocument | None
    alternatives: Annotated[
        list[ReportCpicRecommendationDocument],
        Constraints(
            description="Other CPIC recommendations the uncertainty or a listed medication's effect could lead to; not guidance for this person."
        ),
    ]
    notes: list[str]


class ReportContextGeneDocument(TypedDict):
    gene: str
    baseline: Annotated[
        str, Constraints(description="The genetic result, without medications.")
    ]
    adjusted: Annotated[
        str,
        Constraints(
            description="The phenotype with the listed medications, by a CPIC-cited rule; the baseline when no listed medication has a CPIC-stated effect."
        ),
    ]
    possible: Annotated[
        list[str],
        Constraints(description="Phenotypes a CPIC-cited rule says may result."),
    ]
    unclear: bool
    rule: str | None
    trigger: Annotated[
        str | None, Constraints(description="The listed medication the rule is for.")
    ]
    quote: Annotated[
        str | None,
        Constraints(description="CPIC's statement the rule rests on, verbatim."),
    ]
    cpic_recommendation: str | None


class ReportFactDocument(TypedDict):
    name: str
    label: str
    value: object
    text: str
    source: Annotated[str | None, Constraints(description="detected or override.")]
    method: str | None
    confidence: str | None
    file: str | None
    at: str | None


class ReportMetricDocument(TypedDict):
    name: str
    label: str
    value: object
    text: str


ReportFindingSection = Literal[
    "lineage",
    "pharmacogenomics",
    "carrier_screening",
    "clinvar",
    "acmg_research",
    "structural_variants",
    "medication_review",
]


class ReportFindingRequired(TypedDict):
    section: ReportFindingSection
    label: Annotated[str, Constraints(minLength=1)]
    value: str
    detail: str | None
    gene: str | None
    note: Annotated[
        str | None,
        Constraints(
            description="A caveat from the gene list, e.g. only biallelic calls are reportable."
        ),
    ]
    confidence: Annotated[
        str, Constraints(enum=("high", "moderate", "low", "not_assessed"))
    ]
    confidence_reasons: list[str]
    confidence_basis: Annotated[
        str, Constraints(description="What the label was derived from.")
    ]
    evidence: ReportEvidenceDocument
    variant: ReportVariantDocument | None
    links: list[ReportLinkDocument]
    disclaimer: Annotated[
        str,
        Constraints(
            enum=(
                "Technical result, not diagnosis.",
                "Research use only; not a diagnosis.",
                "Not a prescription; review with a clinician.",
            ),
            description="Research use only for acmg_research findings; not a prescription for medication_review findings.",
        ),
    ]


class ReportFindingDocument(ReportFindingRequired, total=False):
    pgx: ReportPgxDocument
    advisory: ReportAdvisoryDocument


class ReportGeneListRequired(TypedDict):
    name: str
    version: Annotated[str, Constraints(description="Empty for a user's file.")]
    citation: Annotated[str, Constraints(description="Empty for a user's file.")]
    url: Annotated[
        str,
        Constraints(
            description="The published gene table a built-in list was checked against; empty for a user's file."
        ),
    ]
    genes: Annotated[int, Constraints(minimum=1)]


class ReportGeneListDocument(ReportGeneListRequired, total=False):
    id: Annotated[
        str,
        Constraints(
            description="A built-in list's id (acmg-sf-3.3), or the name of the user's TSV or BED file."
        ),
    ]


class ReportSectionRequired(TypedDict):
    title: str
    status: Annotated[
        Literal["ok", "not_run", "failed"],
        Constraints(
            description="ok: at least one source run succeeded; not_run: no run found (see missing); failed: only failed runs found (see error)."
        ),
    ]
    summary: str
    findings: list[ReportFindingDocument]
    missing: Annotated[
        list[ReportMissingDocument],
        Constraints(
            description="Parts of the section not run yet, with the command that produces each (relative to the sample folder)."
        ),
    ]
    warnings: list[ReportWarningDocument]
    sources: Annotated[
        list[ReportSourceDocument],
        Constraints(
            description="The result manifests the section was built from: the selected evidence's status and finish time, and the newest attempt when that is newer."
        ),
    ]


class ReportSectionDocument(ReportSectionRequired, total=False):
    error: str


class ReportOverviewDocument(ReportSectionDocument):
    facts: list[ReportFactDocument]
    qc: list[ReportMetricDocument]


class ReportAncestryComponentDocument(TypedDict):
    k: Annotated[int, Constraints(minimum=1)]
    name: str
    region: str
    proportion: Annotated[float, Constraints(minimum=0, maximum=1)]
    lower: Annotated[float, Constraints(minimum=0, maximum=1)]
    upper: Annotated[float, Constraints(minimum=0, maximum=1)]
    reference_populations: str


class ReportAncestryRequired(ReportSectionDocument):
    note: str
    components: list[ReportAncestryComponentDocument]


# Optional fragments let writers compose sections without inserting default keys.
class ReportAncestryFields(TypedDict, total=False):
    evidence: str | None
    metrics: dict[str, object]


class ReportAncestryDocument(ReportAncestryRequired, ReportAncestryFields):
    pass


NonnegativeCount = Annotated[int, Constraints(minimum=0)]
ReportBuild = Annotated[str | None, Constraints(enum=("hg19", "hg38", None))]


class ReportGeneDocument(TypedDict):
    gene: str
    inheritance: Annotated[
        str | None,
        Constraints(
            description="The gene list's mode of inheritance (AD, AR, SD, XL); null when it gives none."
        ),
    ]
    heterozygous: NonnegativeCount
    homozygous: NonnegativeCount
    hemizygous: Annotated[
        NonnegativeCount,
        Constraints(description="Haploid calls (one allele in the genotype)."),
    ]
    partial: Annotated[
        NonnegativeCount,
        Constraints(description="Calls whose other allele was not called (1/.)."),
    ]
    phase: Annotated[
        str | None,
        Constraints(
            enum=("trans", "cis", "unknown", None),
            description="The phase of two or more heterozygous calls: trans when two are on opposite copies (two ALTs of one call such as 1/2, or phased in one phase set), cis when all are phased on one copy in one phase set, else unknown (unphased, phased without a PS, or in different phase sets). Null for fewer than two, and when the calls are incomplete (calls_truncated) unless trans.",
        ),
    ]
    confidence: Annotated[
        str,
        Constraints(
            enum=("high", "moderate", "low", "not_assessed"),
            description="The weakest confidence label of the gene's calls.",
        ),
    ]
    status: Annotated[
        str | None,
        Constraints(
            enum=("biallelic", "possible_biallelic", "carrier", None),
            description="For a gene the list reports only when biallelic (AR): biallelic (a homozygous call, or two heterozygous calls in trans), possible_biallelic (two or more heterozygous calls not all phased together, or a hemizygous or partly called genotype) or carrier (one heterozygous call, or several in cis). Null for other genes, and when the calls are incomplete (calls_truncated) unless biallelic.",
        ),
    ]
    status_text: Annotated[
        str, Constraints(description="The status explained; empty when status is null.")
    ]


class ReportClinvarCountsRequired(TypedDict):
    carried: NonnegativeCount
    in_gene_list: NonnegativeCount
    outside_gene_list: NonnegativeCount
    high: NonnegativeCount
    moderate: NonnegativeCount
    low: NonnegativeCount


class ReportClinvarCountsDocument(ReportClinvarCountsRequired, total=False):
    unplaced: NonnegativeCount
    conflicts_for_review: NonnegativeCount


class ReportGeneSectionDocument(ReportSectionDocument):
    gene_list: ReportGeneListDocument


class ReportClinvarFields(TypedDict, total=False):
    build: ReportBuild
    all_calls: Annotated[
        str | None,
        Constraints(
            description="Every carried ClinVar P/LP call (confidence TSV), relative to the folder."
        ),
    ]
    all_conflicts: Annotated[
        str | None,
        Constraints(
            description="Conflicting ClinVar assertions for review, not consensus P/LP, relative to the folder."
        ),
    ]
    counts: ReportClinvarCountsDocument
    genes: Annotated[
        list[ReportGeneDocument],
        Constraints(
            description="Each listed gene with P/LP calls, counted by zygosity."
        ),
    ]


class ReportClinvarDocument(ReportGeneSectionDocument, ReportClinvarFields):
    pass


class ReportAcmgCountsDocument(TypedDict):
    p: NonnegativeCount
    lp: NonnegativeCount
    vus: NonnegativeCount
    lb: NonnegativeCount
    b: NonnegativeCount
    in_gene_list: NonnegativeCount
    outside_gene_list: NonnegativeCount
    unplaced: NonnegativeCount


class ReportAcmgRequired(ReportGeneSectionDocument):
    disclaimer: Annotated[
        str,
        Constraints(
            description="The research-only disclaimer, shown at the top of the section."
        ),
    ]


class ReportAcmgFields(TypedDict, total=False):
    tool: Annotated[
        str | None,
        Constraints(
            description="The classifier and its version, e.g. 'fastVEP 0.5.0'."
        ),
    ]
    criteria_not_evaluated: Annotated[
        list[str],
        Constraints(description="ACMG/AMP criteria the classifier never evaluated."),
    ]
    build: ReportBuild
    all_calls: Annotated[
        str | None,
        Constraints(
            description="The classified VCF (acmg_annotated.vcf.gz), relative to the folder."
        ),
    ]
    counts: Annotated[
        ReportAcmgCountsDocument,
        Constraints(
            description="Carried alleles by automated class (p, lp, vus, lb, b), and the carried P/LP calls in_gene_list (the findings), outside_gene_list and unplaced."
        ),
    ]


class ReportAcmgDocument(ReportAcmgRequired, ReportAcmgFields):
    pass


class ReportSvThresholdsDocument(TypedDict, total=False):
    common_af: float
    rare_af: float


class ReportSvFields(TypedDict, total=False):
    all_svs: Annotated[
        str | None,
        Constraints(
            description="Every SV with its frequency class (sv_freq.tsv), relative to the folder."
        ),
    ]
    thresholds: ReportSvThresholdsDocument
    counts: Annotated[
        dict[str, NonnegativeCount],
        Constraints(
            description="Carried SVs by frequency class (common, low, rare, novel), and listed: the rare SVs shown as findings."
        ),
    ]


class ReportSvDocument(ReportSectionDocument, ReportSvFields):
    pass


class ReportMicroarrayFileDocument(TypedDict):
    file: str | None
    role: str
    format: str | None
    exists: bool
    sites: int | None
    called: int | None
    no_call_rate: float | None


class ReportMicroarrayRequired(ReportSectionDocument):
    files: list[ReportMicroarrayFileDocument]


class ReportMicroarrayFields(TypedDict, total=False):
    metrics: dict[str, object]


class ReportMicroarrayDocument(ReportMicroarrayRequired, ReportMicroarrayFields):
    pass


class ReportCpicReleaseDocument(TypedDict):
    release: str
    published: str
    sha256: str
    url: str
    license: str
    attribution: str


class ReportContextRulesDocument(TypedDict):
    version: str
    sha256: str
    checked_against_cpic: str


class ReportContextMedicationDocument(TypedDict):
    name: str
    population: str | None
    cpic_drug: Annotated[
        str | None, Constraints(description="CPIC's drug id; null when not in CPIC.")
    ]


class ReportPgxContextDocument(TypedDict):
    sha256: Annotated[
        str,
        Constraints(
            description="SHA-256 of the effective medication list (names and populations), not of the file."
        ),
    ]
    medications: list[ReportContextMedicationDocument]


class ReportMedicationReviewDocument(ReportSectionDocument):
    disclaimer: str
    cpic: ReportCpicReleaseDocument
    rules: ReportContextRulesDocument
    context: ReportPgxContextDocument
    genes: Annotated[
        list[ReportContextGeneDocument],
        Constraints(
            description="Each gene the listed medications' CPIC guidance uses: its genetic (baseline) and medication-adjusted phenotype."
        ),
    ]


class ReportSectionsRequired(TypedDict):
    overview: ReportOverviewDocument
    lineage: ReportSectionDocument
    pharmacogenomics: ReportSectionDocument
    clinvar: ReportClinvarDocument
    structural_variants: ReportSvDocument
    microarray: ReportMicroarrayDocument


class ReportResearchSection(TypedDict, total=False):
    acmg_research: Annotated[
        ReportAcmgDocument,
        Constraints(
            description="fastVEP's automated ACMG/AMP classes (vcf acmg), research use only and not a diagnosis. Present only when vcf acmg ran."
        ),
    ]


class ReportSectionsDocument(
    ReportSectionsRequired, ReportResearchSection, total=False
):
    ancestry: ReportAncestryDocument
    carrier_screening: ReportSectionDocument
    medication_review: Annotated[
        ReportMedicationReviewDocument,
        Constraints(
            description="CPIC guidance for the medications in --pgx-context, gated on the pharmacogenomic calls' evidence; not a prescription. Present only with --pgx-context."
        ),
    ]


class ReportSampleDocument(TypedDict):
    name: Annotated[
        str,
        Constraints(
            description="The sample's name: its alignment's (or VCF's) file name without extensions."
        ),
    ]
    folder: Annotated[
        str,
        Constraints(
            description="The sample folder's name (not its full path). Every file path in the report is relative to this folder."
        ),
    ]
    alignment: Annotated[
        str | None,
        Constraints(
            description="The BAM/CRAM the report describes, relative to the folder."
        ),
    ]
    vcf: Annotated[
        str | None,
        Constraints(
            description="The sample's VCF used in hints, relative to the folder."
        ),
    ]
    workspace: Annotated[
        str | None,
        Constraints(description="The sample workspace file, when there is one."),
    ]


class ReportDocument(TypedDict):
    schema: Literal["wgsextract.report/1"]
    generated: Annotated[
        str,
        Constraints(format="date-time", description="When the report was built (UTC)."),
    ]
    wgsextract_version: str
    sample: ReportSampleDocument
    disclaimer: str
    guide: str
    confidence_reasons: Annotated[
        dict[str, str],
        Constraints(description="Reason code -> plain-language explanation."),
    ]
    sections: ReportSectionsDocument


def section_items(
    sections: ReportSectionsDocument,
) -> ItemsView[str, ReportSectionDocument]:
    # This closed, writer-built map has a shared base that TypedDict.items erases.
    return cast(Mapping[str, ReportSectionDocument], sections).items()
