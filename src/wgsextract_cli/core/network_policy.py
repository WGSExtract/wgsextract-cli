"""Consent for first-use downloads, scoped to a CLI/API operation (#1052)."""

from wgsextract_cli.core.execution_context import current
from wgsextract_cli.core.utils import UsageError


class DownloadRequired(UsageError):
    """Public data is missing and this operation has no download consent."""


def require_download(resource: str, download_command: str | None = None) -> None:
    """Refuse an implicit fetch before any server is contacted.

    Unscoped resource installers remain explicit Python download operations;
    CLI/API dispatch always binds a permission, inherited by worker threads.
    """
    context = current()
    if context.allow_downloads is not False:
        return
    command = context.result.command if context.result else "this command"
    hint = (
        f" Or download it first with '{download_command}'." if download_command else ""
    )
    raise DownloadRequired(
        f"Missing public reference data: {resource}. Rerun '{'wgsextract ' + command} "
        f"--download' to confirm the download (see docs/privacy.md).{hint}"
    )
