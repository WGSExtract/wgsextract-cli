"""Operation exceptions shared by the CLI and its implementations."""


class WGSExtractError(Exception):
    """Base exception for wgsextract-cli errors (exit code 1)."""


class CodedError(WGSExtractError):
    """An operation failure with a stable, machine-readable message code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
