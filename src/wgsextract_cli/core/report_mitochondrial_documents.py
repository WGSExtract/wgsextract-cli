"""Typed evidence and provenance for mitochondrial report sections."""

from typing import Annotated, TypedDict

from wgsextract_cli.core.output_schema import Constraints

Fraction = Annotated[float, Constraints(minimum=0, maximum=1)]
MitoState = Annotated[
    str,
    Constraints(
        enum=(
            "homoplasmic",
            "heteroplasmic",
            "weak_evidence",
            "not_detected",
            "not_assessed",
        ),
        description="homoplasmic: mutserve called the ALT at 95% or more; heteroplasmic: a call at 10-95%; weak_evidence: a call below 10%, or ALT reads (1% or more on a strand) mutserve didn't call; not_detected: no ALT read at 100x or more (a supported negative for heteroplasmy of 10% or more); not_assessed: no or too few reads, an unconfirmed reference or conflicting evidence (see reasons).",
    ),
]


class ReportMitoSiteDocument(TypedDict):
    group: Annotated[
        str,
        Constraints(
            enum=("disease", "aminoglycoside"),
            description="disease: a ClinVar expert-panel pathogenic SNV; aminoglycoside: a CPIC MT-RNR1 increased-risk allele.",
        ),
    ]
    gene: str
    hgvs: Annotated[str, Constraints(description="On the rCRS, e.g. m.1555A>G.")]
    pos: Annotated[int, Constraints(minimum=1, description="rCRS position.")]
    ref: str
    alt: str
    state: MitoState
    level: Annotated[
        float | None,
        Constraints(
            minimum=0,
            maximum=1,
            description="The ALT's share of reads: mutserve's level for a call, else the read fraction; null when not measured.",
        ),
    ]
    depth: Annotated[int | None, Constraints(minimum=0)]
    depth_fwd: Annotated[int | None, Constraints(minimum=0)]
    depth_rev: Annotated[int | None, Constraints(minimum=0)]
    level_fwd: Annotated[float | None, Constraints(minimum=0, maximum=1)]
    level_rev: Annotated[float | None, Constraints(minimum=0, maximum=1)]
    confidence: Annotated[
        str, Constraints(enum=("high", "moderate", "low", "not_assessed"))
    ]
    reasons: list[str]
    clinvar_id: Annotated[str, Constraints(description="ClinVar Variation ID.")]
    clinvar_classification: str
    clinvar_review_status: str
    clinvar_stars: Annotated[int | None, Constraints(minimum=0, maximum=4)]
    conditions: list[str]
    cpic_phenotype: Annotated[
        str | None,
        Constraints(description="CPIC's phenotype for an aminoglycoside allele."),
    ]
    cpic_evidence: Annotated[
        str | None,
        Constraints(description="CPIC's level of evidence for the allele."),
    ]


class ReportMitoTechnicalDocument(TypedDict):
    caller: str
    version: str | None
    settings: Annotated[str, Constraints(description="mutserve's call settings.")]
    mt_reference: Annotated[
        str | None,
        Constraints(
            description="The mtDNA reference the reads were called against: rCRS or CHM13v2.0's chrM; null when unconfirmed (reviewed variants are then not assessed)."
        ),
    ]
    contig: str | None
    reference_md5: str | None
    median_depth: float | None
    min_depth_fraction: Annotated[
        float | None,
        Constraints(
            minimum=0,
            maximum=1,
            description="Share of mtDNA positions with at least 100 reads.",
        ),
    ]
    homoplasmies: Annotated[int | None, Constraints(minimum=0)]
    heteroplasmies: Annotated[int | None, Constraints(minimum=0)]
    contamination: Annotated[
        str | None,
        Constraints(description="haplocheck's contamination status (YES, NO, ND)."),
    ]
    calls: Annotated[
        str | None,
        Constraints(description="mutserve's VCF, relative to the folder."),
    ]
    site_evidence: Annotated[
        str | None,
        Constraints(
            description="mutserve's per-position read table, relative to the folder."
        ),
    ]


class ReportMitoThresholdsDocument(TypedDict):
    homoplasmic: Fraction
    supported_heteroplasmy: Fraction
    weak_evidence: Fraction
    min_depth: Annotated[int, Constraints(minimum=1)]


class ReportMitoResourceDocument(TypedDict):
    id: str
    name: str
    clinvar_release: str
    clinvar_url: str
    clinvar_md5: str
    clinvar_selection: str
    cpic_citation: str
    cpic_url: str
    heteroplasmy_basis: Annotated[
        str, Constraints(description="CPIC's position on heteroplasmy levels.")
    ]
    variants: Annotated[int, Constraints(minimum=1)]
