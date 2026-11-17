"""The sample-show view built for the CLI and API, not the workspace TOML."""

from typing import Literal, TypedDict

from .sample_types import SampleSchema

FileStatus = Literal["current", "changed", "missing"]


class FactRequired(TypedDict):
    name: str
    value: object
    source: Literal["override", "detected"]


class FactView(FactRequired, total=False):
    statistics: dict[str, float]
    evidence_fingerprint: str
    file: str
    fingerprint: str
    method: str
    confidence: str
    version: int
    by: str
    at: str


class OverrideView(TypedDict):
    status: FileStatus
    fingerprint: object
    values: dict[str, object]


class FileRequired(TypedDict):
    status: FileStatus
    facts: dict[str, FactView]


class FileView(FileRequired, total=False):
    fingerprint: object
    overrides: OverrideView


class WorkspaceRequired(TypedDict):
    schema: SampleSchema
    workspace: str
    path: str
    exists: bool
    problem: str | None
    genome_folder: bool
    overrides: dict[str, str]
    files: dict[str, FileView]
    outputs: dict[str, dict[str, object]]


class WorkspaceView(WorkspaceRequired, total=False):
    file: str
    effective: dict[str, FactView]
