"""Wire documents built by the server, shared by schema and client generation.

Loaded result/report documents remain opaque at this boundary: reading their
schema identifier is not validation of all their fields.
"""

from typing import Annotated, Literal, TypedDict

from wgsextract_cli.core.output_schema import Constraints

Kind = Literal["query", "job"]
Status = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
Timestamp = Annotated[str, Constraints(format="date-time")]


class ErrorDetail(TypedDict):
    code: str
    message: str


class Error(TypedDict):
    error: ErrorDetail


class Health(TypedDict):
    ok: bool
    version: str


class Index(TypedDict):
    name: str
    version: str
    ops: str
    jobs: str
    openapi: str
    lookups: list[str]
    allowed_paths: list[str]


class OperationRequired(TypedDict):
    op: str
    kind: Kind
    summary: str


class OperationSummary(OperationRequired, total=False):
    destructive: bool


class OperationDescription(OperationSummary):
    command: str
    params: dict[str, object]


class Operations(TypedDict):
    ops: list[OperationSummary]


class JobSummaryRequired(TypedDict):
    id: str
    op: str
    status: Status
    created: Timestamp


class JobSummary(JobSummaryRequired, total=False):
    finished: Timestamp


class Jobs(TypedDict):
    jobs: list[JobSummary]


class JobRequired(JobSummaryRequired):
    kind: Kind


class JobProgress(TypedDict, total=False):
    stage: str
    message: str
    fraction: float


class JobEventRequired(TypedDict):
    seq: int
    type: Literal["status", "progress"]
    time: Timestamp


class JobEvent(JobProgress, JobEventRequired, total=False):
    status: Status | Literal["cancelling"]


class JobError(TypedDict, total=False):
    kind: str
    message: str


class Job(JobRequired, total=False):
    started: Timestamp
    finished: Timestamp
    exit_code: int
    error: JobError
    progress: JobProgress
    result: dict[str, object]
    cancel_requested: Timestamp
    params: dict[str, object]


class VariantPageRequired(TypedDict):
    path: str
    rows: list[dict[str, object]]
    next_cursor: str | None


class VariantPage(VariantPageRequired, total=False):
    # acmg.DISCLAIMER, when a row has an acmg_* column: research use only.
    disclaimer: str
