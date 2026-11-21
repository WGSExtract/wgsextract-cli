"""Operation exceptions and the structured error recorded in results."""

from dataclasses import dataclass
from typing import Annotated, Literal

from wgsextract_cli.core.output_schema import Constraints

ErrorKind = Literal["failure", "usage", "missing_dependency", "cancelled"]


class WGSExtractError(Exception):
    """Base exception for wgsextract-cli errors (exit code 1)."""


class CodedError(WGSExtractError):
    """An operation failure with a stable, machine-readable message code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ResultError:
    kind: ErrorKind
    message: str
    code: Annotated[str, Constraints(pattern="^[a-z][a-z0-9_]*$")] | None = None
