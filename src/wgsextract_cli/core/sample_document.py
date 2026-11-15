"""The wgsextract.sample/1 TOML document, distinct from sample-show JSON."""

from typing import Annotated, TypedDict

from .output_schema import Constraints, Shape
from .sample_types import (
    Confidence,
    Platform,
    SampleSchema,
    Sex,
    Status,
    Superpopulation,
)

Time = Annotated[str, Constraints(format="date-time")]
Nonnegative = Annotated[float, Constraints(minimum=0)]
Fraction = Annotated[float, Constraints(minimum=0, maximum=1)]


class FactMetadata(TypedDict):
    method: Annotated[str, Constraints(description="How it was detected.")]
    confidence: Confidence
    version: Annotated[
        int,
        Constraints(
            description="Detector version; a fact from another version is detected again.",
            minimum=1,
        ),
    ]
    by: Annotated[str, Constraints(description='"wgsextract <version> <command>".')]
    at: Annotated[
        str, Constraints(description="UTC time it was detected.", format="date-time")
    ]


class RecordedFact(FactMetadata):
    value: object


class SexFact(FactMetadata):
    value: Sex


class PlatformFact(FactMetadata):
    value: Platform


class BuildFact(FactMetadata):
    value: str


class SuperpopulationFact(FactMetadata):
    value: Superpopulation


class NonnegativeFact(FactMetadata):
    value: Nonnegative


class FractionFact(FactMetadata):
    value: Fraction


class AncestryFact(FactMetadata):
    value: dict[str, Fraction]


class DepthsFact(FactMetadata):
    value: dict[str, Nonnegative]


class FileFingerprint(TypedDict):
    fingerprint: Annotated[
        str,
        Constraints(
            description="The file's wgse-fp1 content fingerprint when the facts were detected (see the result manifest).",
            pattern="^wgse-fp1:[0-9a-f]{64}$",
        ),
    ]


class DetectedFile(FileFingerprint, total=False):
    sex: SexFact
    platform: PlatformFact
    build: BuildFact
    somalier_sex: SexFact
    superpopulation: SuperpopulationFact
    read_length: NonnegativeFact
    mean_coverage: NonnegativeFact
    contamination: FractionFact
    ancestry: AncestryFact
    target_depths: DepthsFact


class OverrideFingerprint(TypedDict):
    fingerprint: str


class FileOverride(OverrideFingerprint, total=False):
    sex: Sex
    platform: Platform


class Overrides(TypedDict, total=False):
    sex: Sex
    platform: Platform


class WorkspaceOutput(TypedDict):
    command: str
    status: Status
    at: Time


class SampleHeader(TypedDict):
    schema: SampleSchema


class SampleDocument(SampleHeader, total=False):
    override: Annotated[
        Overrides,
        Constraints(
            description="Facts the user set; commands use them instead of what they detect, and warn when the reads disagree. A value here is folder-wide, honoured for sex in a genome folder only; a table holds one file's overrides (path relative to the workspace, or absolute), honoured while its fingerprint matches."
        ),
    ]
    detected: Annotated[
        dict[str, DetectedFile],
        Constraints(
            description="Per file (path relative to the workspace, or absolute), the facts detected from it. They hold only while the file's fingerprint matches."
        ),
    ]
    outputs: Annotated[
        dict[str, WorkspaceOutput],
        Constraints(
            description="Result manifests (<output>.manifest.json) written in the workspace by --genome runs."
        ),
    ]


SAMPLE_SHAPES: dict[object, Shape] = {
    Sex: Shape("sex"),
    Platform: Shape("platform"),
    RecordedFact: Shape("fact"),
    SexFact: Shape("sex_fact"),
    PlatformFact: Shape("platform_fact"),
    BuildFact: Shape("build_fact"),
    SuperpopulationFact: Shape("superpopulation_fact"),
    NonnegativeFact: Shape("nonnegative_fact"),
    FractionFact: Shape("fraction_fact"),
    AncestryFact: Shape("ancestry_fact"),
    DepthsFact: Shape("depths_fact"),
    DetectedFile: Shape("file", additional=RecordedFact),
    FileOverride: Shape("file_override", additional=False),
    Overrides: Shape("override", additional=FileOverride),
    WorkspaceOutput: Shape("output"),
    SampleDocument: Shape("sample"),
}

SCHEMA_DESCRIPTION = (
    "A sample workspace: wgsextract.sample.toml in a genome folder, as parsed from TOML. "
    "Records the facts commands detected from each of the sample's files, the user's "
    "overrides and the result manifests written there. Unknown top-level keys are kept. "
    "See docs/sample-workspace.md."
)
