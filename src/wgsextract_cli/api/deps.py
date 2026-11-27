"""``deps check`` and ``deps list``: which bioinformatics tools this
installation finds, where, and in what version; ``deps install`` and
``deps uninstall``: add or remove an optional tool's Pixi environment.

``check`` without ``tool`` lists every mandatory and optional tool. The
result's ``details`` hold ``mandatory`` and ``optional``: one
``{name, path, runtime, version, alt_env}`` per tool (``path`` is ``None`` for
a missing one). It fails with exit code 3 (error kind
``missing_dependency``) if a mandatory tool is missing. With ``tool``, it
checks that one tool: its path is ``details["path"]`` and the result's text,
and a missing tool fails the same way, naming the command that installs it
when there is one.

``list`` reports the same, plus ``details["tools"]``: every tool, those not
built for this platform included, with its ``group`` ("mandatory" or
"optional"), ``status`` ("installed", "missing" or "unavailable"), its own
Pixi ``environment`` and the ``environment_tools`` it provides,
``can_install`` and ``can_uninstall``, and a ``note`` (how to install it
yourself, or why it can't be had). It doesn't fail on a missing tool.

``ready`` reports whether a command line (``command_line``, as its argv
after ``wgsextract``) can run on this computer without the network: each
prerequisite its run checks (platform, tools, Docker images and GPUs, model
weights and their SHA-256, the input, reference genome and indexes, and
annotation data), with a ``status`` (runnable, installed, unverified,
unsupported, missing, stale or incompatible), its evidence (version, path,
Conda package and the licence its metadata records, image identity and
labels, digests; terms are "unknown" where nothing local records them) and
the explicit step that provides it. ``details["status"]`` is "ready",
"ready_unverified" or "not_ready"; not ready fails with exit code 3 (error
kind ``missing_dependency``), with the details still set. It installs,
pulls and downloads nothing. The server doesn't offer it: the command line
names host paths its folder checks can't vet.

``install`` downloads and installs the Pixi environment that provides an
optional tool (``pixi install --locked -e <env>``); ``uninstall`` removes it
(``pixi clean -e <env>``), and with it the other tools in that environment.
Tools that come with the CLI itself, or that you install yourself, are
refused with how to get them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wgsextract_cli.core.results import CommandResult

from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute


@dataclass(frozen=True, kw_only=True)
class DepsCheckParams(Params):
    """Check the installed tools, or one ``tool``."""

    COMMAND = "deps check"

    tool: str | None = param(
        None,
        help="Check for a specific tool; exit code 3 if it is missing.",
        doc="The one tool to check (e.g. samtools); fails if it is missing.",
        may_name_file=True,
    )


@dataclass(frozen=True, kw_only=True)
class DepsListParams(Params):
    """List every tool, its status and whether it can be installed or removed."""

    COMMAND = "deps list"


@dataclass(frozen=True, kw_only=True)
class DepsReadyParams(Params):
    """Report whether a command line can run here without the network."""

    COMMAND = "deps ready"
    LOCAL_ONLY = True

    command_line: tuple[str, ...] = param(
        help="The wgsextract command line to check, after --.",
        doc="The command line to check: its arguments after ``wgsextract``.",
        remainder=True,
        metavar="-- COMMAND ...",
        may_name_file=True,
    )


def _tool_param() -> Any:
    return param(
        help="The optional tool, as 'deps list' names it (e.g. gatk).",
        doc="The optional tool, as list names it (e.g. gatk).",
        positional=True,
        metavar="TOOL",
    )


@dataclass(frozen=True, kw_only=True)
class DepsInstallParams(Params):
    """Download and install an optional tool."""

    COMMAND = "deps install"
    LOCAL_ONLY = True

    tool: str = _tool_param()
    accept_license: bool = param(
        False,
        help="Accept the tool's licence (dorado: research use only).",
        action="store_true",
    )


@dataclass(frozen=True, kw_only=True)
class DepsUninstallParams(Params):
    """Remove the Pixi environment providing an optional tool."""

    COMMAND = "deps uninstall"
    LOCAL_ONLY = True
    DELETES = True

    tool: str = _tool_param()


def check(
    params: DepsCheckParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Check the installed tools (see ``DepsCheckParams``)."""
    from wgsextract_cli.commands._deps_status import run

    return execute(params, run, progress=progress, cancel=cancel, check=check)


def list_tools(
    params: DepsListParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """List every tool and its status (see ``DepsListParams``)."""
    from wgsextract_cli.commands._deps_status import run_list

    return execute(params, run_list, progress=progress, cancel=cancel, check=check)


def ready(
    params: DepsReadyParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Report a command line's offline readiness (see ``DepsReadyParams``)."""
    from wgsextract_cli.commands._deps_status import run_ready

    return execute(params, run_ready, progress=progress, cancel=cancel, check=check)


def install(
    params: DepsInstallParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Install an optional tool's Pixi environment (see ``DepsInstallParams``)."""
    from wgsextract_cli.commands._deps_status import run_install

    return execute(params, run_install, progress=progress, cancel=cancel, check=check)


def uninstall(
    params: DepsUninstallParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Remove an optional tool's Pixi environment (see ``DepsUninstallParams``)."""
    from wgsextract_cli.commands._deps_status import run_uninstall

    return execute(params, run_uninstall, progress=progress, cancel=cancel, check=check)


__all__ = [
    "DepsCheckParams",
    "DepsInstallParams",
    "DepsListParams",
    "DepsReadyParams",
    "DepsUninstallParams",
    "check",
    "install",
    "list_tools",
    "ready",
    "uninstall",
]
