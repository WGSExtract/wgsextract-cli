"""Outbound transfer policy and first-use consent, scoped to an operation."""

from collections.abc import Iterator
from contextlib import contextmanager

from wgsextract_cli.core.docs_links import doc_url
from wgsextract_cli.core.execution_context import bound, current
from wgsextract_cli.core.utils import UsageError


class DownloadRequired(UsageError):
    """Public data is missing and this operation has no download consent."""


class NetworkRestricted(UsageError):
    """An outbound operation was refused by the selected network mode."""


def network_mode() -> str:
    from wgsextract_cli.core.config import settings

    return current().network_mode or settings.get("network_mode", "full")


@contextmanager
def network_options(mode: str | None, allow_large: bool) -> Iterator[None]:
    context = current()
    with bound(
        network_mode=mode or context.network_mode,
        allow_large_download=allow_large or context.allow_large_download,
    ):
        yield


def require_network(operation: str, *, large: bool = True) -> None:
    """Check before opening a connection or launching a network-capable tool.

    Transfers are bulk by default; callers must identify small requests.
    This check never grants an operation's separate download or disclosure opt-in.
    """
    mode = network_mode()
    if mode == "off":
        raise NetworkRestricted(
            f"{operation}: network access is disabled by --network off "
            '(network_mode = "off" in config.toml).'
        )
    if mode == "low" and large and not current().allow_large_download:
        raise NetworkRestricted(
            f"{operation}: --network low (network_mode in config.toml) "
            "requires --allow-large-download for this transfer."
        )


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
        f"--download' to confirm the download (see {doc_url('privacy.md')}).{hint}"
    )
