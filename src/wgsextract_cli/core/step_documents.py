"""Writer models for internal step journals, not assertions about loaded data."""

from collections.abc import Mapping
from typing import Final, Literal, TypedDict, cast

SCHEMA: Final = "wgsextract.steps/1"


class DirectoryIdentity(TypedDict):
    files: int
    size: int
    fingerprint: str


class Digest(TypedDict):
    """How a recorded fingerprint was made, and so what it can vouch for."""

    algorithm: Literal["sha256", "wgse-fp1", "wgse-dir1"]
    strength: Literal["full", "sampled", "metadata"]


class FileIdentity(TypedDict):
    size: int
    mtime_ns: int
    fingerprint: str | None
    digest: Digest


class InputRequired(TypedDict):
    role: str
    path: str


class InputIdentity(InputRequired, total=False):
    size: int | None
    fingerprint: str | None
    mtime_ns: int
    digest: Digest
    from_step: str
    from_run: object
    kind: Literal["directory"]
    files: int
    missing: bool
    declared: bool


class FileOutput(TypedDict):
    path: str
    size: int
    mtime_ns: int
    fingerprint: str | None
    digest: Digest


class DirectoryOutputRequired(TypedDict):
    path: str
    kind: Literal["directory"]
    files: int
    size: int
    fingerprint: str
    digest: Digest


class DirectoryOutput(DirectoryOutputRequired, total=False):
    # Declared required members (relative path -> identity). Without them
    # every file but locks and staged partials is required.
    members: dict[str, FileIdentity]


OutputDocument = FileOutput | DirectoryOutput


def extend_output(
    identity: OutputDocument, details: Mapping[str, object]
) -> OutputDocument:
    # Each variant declares only required fields; typed writer values win over
    # arbitrary extension keys without asserting anything about loaded records.
    return cast(OutputDocument, {**details, **identity})


class RunningStepDocument(TypedDict):
    status: Literal["running"]
    token: str
    key: str
    params: dict[str, object]
    started: str
    inputs: list[InputIdentity]


class CompleteStepDocument(TypedDict):
    status: Literal["complete"]
    key: str
    version: int
    params: dict[str, object]
    tools: dict[str, str | None]
    inputs: list[InputIdentity]
    outputs: list[OutputDocument]
    metrics: dict[str, object]
    run_id: str
    # The writer preserves an earlier loaded record's value, even if malformed.
    started: object
    finished: str
    seconds: float


class JournalDocument(TypedDict):
    schema: Literal["wgsextract.steps/1"]
    workflow: str
    # Unclassified loaded records survive unrelated writes without validation.
    steps: dict[str, RunningStepDocument | CompleteStepDocument | dict[str, object]]
