"""Typed report components used at both construction and presentation boundaries."""

from typing import Annotated, TypedDict

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
