"""Typed report components used at both construction and presentation boundaries."""

from typing import Annotated, Literal, TypedDict

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


class ReportClinicalEvidenceDocument(TypedDict, total=False):
    id: str | None
    review_status: str | None
    stars: Annotated[int | None, Constraints(minimum=0, maximum=4)]
    review_tier: str
    conflict: bool
    conflict_evidence: str | None


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
    clinical_evidence: ReportClinicalEvidenceDocument
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
            ),
            description="Research use only for acmg_research findings.",
        ),
    ]


class ReportFindingDocument(ReportFindingRequired, total=False):
    pgx: ReportPgxDocument
