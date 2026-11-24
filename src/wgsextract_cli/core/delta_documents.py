"""The wgsextract.delta/1 document: report-delta's details (#1179)."""

from typing import Annotated, Literal, TypedDict

from wgsextract_cli.core.output_schema import Constraints

ReleaseChange = Literal[
    "same", "changed", "content_changed", "added", "removed", "unknown"
]
FindingChange = Literal[
    "added",
    "removed",
    "reclassified",
    "review_changed",
    "genotype_changed",
    "result_changed",
    "confidence_changed",
    "ambiguity_changed",
]
ChangeArea = Literal[
    "status", "warning", "metric", "output", "version", "tool", "reference", "gene"
]


class DeltaRunDocument(TypedDict):
    manifest: str
    command: str
    status: str
    finished: str | None
    version: str | None
    sample: Annotated[
        str | None,
        Constraints(
            description="Report sample, else the sample the run recorded "
            "selecting (metrics.sample), else the input's file name."
        ),
    ]
    build: str | None
    resources_recorded: Annotated[
        bool,
        Constraints(
            description="False for a manifest written before releases were recorded."
        ),
    ]


class DeltaReleaseDocument(TypedDict):
    id: str
    name: str
    build: str | None
    before: str | None
    after: str | None
    change: ReleaseChange


class DeltaFindingStateDocument(TypedDict):
    value: str
    detail: str | None
    genotype: str | None
    zygosity: str | None
    confidence: str
    confidence_reasons: list[str]
    review_status: str | None
    stars: int | None
    conflict: Annotated[
        bool | None,
        Constraints(description="ClinVar's submitters conflict on the variant."),
    ]
    conflict_evidence: str | None
    phased_with: Annotated[
        list[str],
        Constraints(
            description="Keys of the section's other findings phased in the same "
            "phase set (on known copies); compared instead of phase-set ids."
        ),
    ]
    pgx_status: str | None


class DeltaFindingDocument(TypedDict):
    section: str
    key: Annotated[
        str,
        Constraints(
            description="chrom:pos:ref:alt for a variant; the label (type, span "
            "and size) for a structural variant or any other finding."
        ),
    ]
    label: str
    gene: str | None
    changes: list[FindingChange]
    before: DeltaFindingStateDocument | None
    after: DeltaFindingStateDocument | None
    confidence: Annotated[
        str,
        Constraints(
            description="The finding's confidence label as reported (after, else before)."
        ),
    ]
    disclaimer: str
    release_changes: Annotated[
        list[str],
        Constraints(
            description="Resources this section rests on whose release changed "
            "between the runs (an association, not a cause)."
        ),
    ]


class DeltaSectionDocument(TypedDict):
    section: str
    title: str
    before_status: str | None
    after_status: str | None
    compared: bool
    not_compared: Annotated[
        list[str],
        Constraints(
            description="Source runs (commands) that failed or didn't run in "
            "either report; their findings are not compared."
        ),
    ]
    note: str | None
    unchanged: int
    findings: list[DeltaFindingDocument]


class DeltaChangeDocument(TypedDict):
    area: ChangeArea
    key: str
    before: object
    after: object


class DeltaSummaryDocument(TypedDict):
    findings_added: int
    findings_removed: int
    findings_changed: int
    findings_unchanged: int
    sections_not_compared: int
    releases_changed: int
    resource_only: Annotated[
        bool,
        Constraints(
            description="Resources changed but no finding, section or run status did."
        ),
    ]


class DeltaDocument(TypedDict):
    schema: Literal["wgsextract.delta/1"]
    before: DeltaRunDocument
    after: DeltaRunDocument
    summary: DeltaSummaryDocument
    releases: list[DeltaReleaseDocument]
    sections: list[DeltaSectionDocument]
    changes: list[DeltaChangeDocument]
    disclaimer: str | None
    limitations: list[str]
