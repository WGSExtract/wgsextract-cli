"""Each tool's state for ``deps list``, and installing or removing an optional
tool for ``deps install`` and ``deps uninstall``.

An optional tool packaged in a Pixi environment of its own (gatk in ``gatk``,
pbmm2, pbsv and sniffles in ``pacbio``) is installed with
``pixi install --locked -e <env>`` and removed with ``pixi clean -e <env>``,
which removes every tool that environment provides. Tools in the ``default``
environment come with the CLI itself and can't be installed or removed on
their own; tools without an environment (plink2, dorado) are the user's to
install (``INSTALL_HINTS``).
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
from typing import Any

from wgsextract_cli.core.native_support import tool_unsupported_reason
from wgsextract_cli.core.pixi_tools import locked_for_platform, pixi_environment_argv
from wgsextract_cli.core.tool_catalog import (
    INSTALL_HINTS,
    MANDATORY_TOOLS,
    OPTIONAL_TOOLS,
    PIXI_SETUP_HINTS,
    PIXI_TOOL_ENVS,
    platform_unavailable_tools,
)
from wgsextract_cli.core.utils import (
    MissingDependencyError,
    UsageError,
    WGSExtractError,
)
from wgsextract_cli.core.windows_arm64 import unsupported_windows_arm64_tool

INSTALLED = "installed"
MISSING = "missing"
UNAVAILABLE = "unavailable"


def own_environment(tool: str) -> str | None:
    """The Pixi environment of its own that provides ``tool``, if any."""
    env = PIXI_TOOL_ENVS.get(tool)
    return env if env and env != "default" else None


def environment_tools(env: str) -> list[str]:
    """The optional tools the Pixi environment ``env`` provides."""
    return [tool for tool in OPTIONAL_TOOLS if PIXI_TOOL_ENVS.get(tool) == env]


def _host() -> str:
    return f"{sys.platform} {platform.machine()}"


def _unavailable_reason(tool: str) -> str | None:
    """Why ``tool`` can't be had on this computer, or None when it can."""
    if tool in platform_unavailable_tools():
        return f"{tool} isn't built for {_host()}."
    reason = unsupported_windows_arm64_tool(tool) or tool_unsupported_reason(tool)
    if reason:
        return reason
    if PIXI_TOOL_ENVS.get(tool) and locked_for_platform(tool) is False:
        return f"No {tool} build is available for {_host()}."
    return None


def _in_use(env: str) -> bool:
    """Whether the CLI itself runs in ``env``: removing it would remove the CLI."""
    return os.environ.get("PIXI_ENVIRONMENT_NAME") == env


def tool_row(row: dict[str, Any], group: str) -> dict[str, Any]:
    """A ``check_all_dependencies`` row with what ``deps list`` adds: the
    tool's ``group``, ``status``, Pixi ``environment`` (when it has one of its
    own), whether ``deps install`` / ``deps uninstall`` can act on it, and a
    ``note`` saying how to get it otherwise or why it can't be had."""
    tool = str(row["name"])
    env = own_environment(tool) if group == "optional" else None
    present = bool(row.get("path"))
    unavailable = None if present or group != "optional" else _unavailable_reason(tool)
    status = INSTALLED if present else UNAVAILABLE if unavailable else MISSING
    can_install = status == MISSING and env is not None
    note = unavailable
    if status == MISSING and not can_install:
        note = INSTALL_HINTS.get(tool)
    elif can_install:
        note = PIXI_SETUP_HINTS.get(tool)
    return row | {
        "group": group,
        "status": status,
        "environment": env,
        "environment_tools": environment_tools(env) if env else [],
        "can_install": can_install,
        # Only a tool found in its own environment: one on PATH is the user's.
        "can_uninstall": bool(
            present and env and row.get("alt_env") == env and not _in_use(env)
        ),
        "note": note,
    }


def unavailable_row(tool: str) -> dict[str, Any]:
    """The ``deps list`` row of an optional tool not built for this platform."""
    return {
        "name": tool,
        "path": None,
        "runtime": None,
        "version": None,
        "alt_env": None,
        "group": "optional",
        "status": UNAVAILABLE,
        "environment": own_environment(tool),
        "environment_tools": [],
        "can_install": False,
        "can_uninstall": False,
        "note": f"{tool} isn't built for {_host()}.",
    }


def _environment_for(tool: str) -> str:
    """The Pixi environment ``deps install`` / ``deps uninstall`` act on for
    ``tool``; a clear error when there is none."""
    if tool in MANDATORY_TOOLS:
        raise UsageError(
            f"{tool} is a mandatory tool that comes with the WGS Extract CLI; "
            "reinstall the CLI to repair it."
        )
    if tool not in OPTIONAL_TOOLS:
        raise UsageError(
            f"Unknown tool {tool!r}. 'wgsextract deps list' lists the tools."
        )
    if tool in platform_unavailable_tools():
        raise WGSExtractError(f"{tool} isn't built for {_host()}.")
    env = PIXI_TOOL_ENVS.get(tool)
    if env is None:
        raise WGSExtractError(
            INSTALL_HINTS.get(tool)
            or f"{tool} isn't installed by WGS Extract: install it yourself and "
            "put it on PATH."
        )
    if env == "default":
        raise WGSExtractError(
            f"{tool} comes with the WGS Extract CLI itself (its default Pixi "
            "environment); reinstall the CLI to repair it."
        )
    return env


def _run_pixi(action: str, env: str) -> None:
    from wgsextract_cli.core.utils import run_command

    argv = pixi_environment_argv(action, env)
    if argv is None:
        raise MissingDependencyError(
            "Pixi isn't installed, so the CLI can't manage its environments. "
            "Install Pixi (https://pixi.sh) or reinstall WGS Extract."
        )
    run_command(argv)


def install(tool: str) -> dict[str, Any]:
    """Download and install the Pixi environment that provides ``tool``."""
    env = _environment_for(tool)
    if reason := _unavailable_reason(tool):
        raise WGSExtractError(reason)
    tools = environment_tools(env)
    logging.info(
        f"Installing the Pixi environment '{env}' (provides {', '.join(tools)})..."
    )
    _run_pixi("install", env)

    from wgsextract_cli.core.dependencies import clear_tool_caches, get_tool_path

    clear_tool_caches()
    path = get_tool_path(tool)
    if not path:
        raise MissingDependencyError(
            f"Installed the Pixi environment '{env}', but {tool} still isn't found."
        )
    if hint := PIXI_SETUP_HINTS.get(tool):
        logging.info(f"{tool} is installed; {hint}")
    return {"tool": tool, "environment": env, "tools": tools, "path": path}


def uninstall(tool: str) -> dict[str, Any]:
    """Remove the Pixi environment that provides ``tool`` (and the other
    tools in it)."""
    env = _environment_for(tool)
    if _in_use(env):
        raise WGSExtractError(
            f"The WGS Extract CLI runs in the Pixi environment '{env}'; it "
            f"can't remove {tool} from it."
        )
    tools = environment_tools(env)
    logging.info(
        f"Removing the Pixi environment '{env}' (provides {', '.join(tools)})..."
    )
    _run_pixi("clean", env)

    from wgsextract_cli.core.dependencies import clear_tool_caches

    clear_tool_caches()
    return {"tool": tool, "environment": env, "tools": tools}


def missing_tools(args: argparse.Namespace) -> list[str]:
    """The optional tools the parsed command line ``args`` needs that aren't
    installed: those every run of the command needs (``COMMAND_NEEDS``), and
    those it uses only on request when an option names one
    (``--caller sniffles``, ``--plink2-check``). Tools this platform can't have are left to the
    command to report."""
    from wgsextract_cli.commands._command_needs import COMMAND_NEEDS
    from wgsextract_cli.core.dependencies import get_tool_path

    command = str(getattr(args, "_command", "") or "")
    needs = COMMAND_NEEDS.get(command)
    if needs is None or command.split()[0] in {"deps", "help", "serve"}:
        return []
    # An option's value (--caller sniffles), or a flag named after the tool
    # (--multiqc, --plink2-check).
    chosen = {
        value if isinstance(value, str) else name.split("_")[0]
        for name, value in vars(args).items()
        if isinstance(value, str) or value is True
    }
    wanted = [*needs.tools, *(tool for tool in needs.optional if tool in chosen)]
    unavailable = set(platform_unavailable_tools())
    return [
        tool
        for tool in dict.fromkeys(wanted)
        if tool in OPTIONAL_TOOLS
        and tool not in unavailable
        and not get_tool_path(tool)
        and not _unavailable_reason(tool)
    ]
