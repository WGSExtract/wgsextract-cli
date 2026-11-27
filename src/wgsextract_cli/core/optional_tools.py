"""Each tool's state for ``deps list``, and installing or removing an optional
tool for ``deps install`` and ``deps uninstall``.

An optional tool packaged in a Pixi environment of its own (gatk in ``gatk``,
pbmm2, pbsv and sniffles in ``pacbio``) is installed with
``pixi install --locked -e <env>`` and removed with ``pixi clean -e <env>``,
which removes every tool that environment provides. Tools in the ``default``
environment come with the CLI itself and can't be installed or removed on
their own; tools without an environment (plink2, dorado) are the user's to
install (``INSTALL_HINTS``), as are tools whose environment has no build for
this platform but that ship a native release (fastvep, wgse-deepvariant).
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import shlex
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core import runtime
from wgsextract_cli.core.native_support import tool_unsupported_reason
from wgsextract_cli.core.pixi_tools import (
    can_manage_environments,
    is_pixi_tool_command,
    locked_for_platform,
    pixi_env_prefix,
    pixi_environment_argv,
)
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


@dataclass(frozen=True)
class ManagedJarTool:
    """An optional Java tool installed by downloading a single verified JAR."""

    version: str
    filename: str
    url: str
    sha256: str
    size_label: str


MANAGED_JAR_TOOLS: dict[str, ManagedJarTool] = {
    "discvrseq": ManagedJarTool(
        version="1.3.92",
        filename="DISCVRSeq-1.3.92.jar",
        url=(
            "https://github.com/BimberLab/DISCVRSeq/releases/download/"
            "1.3.92/DISCVRSeq-1.3.92.jar"
        ),
        sha256="9e135781c445af5a08fb370f513a2e64555bab33273e539ca1aa134ee833e323",
        size_label="~419 MB",
    ),
}


def own_environment(tool: str) -> str | None:
    """The Pixi environment of its own that provides ``tool``, if any."""
    env = PIXI_TOOL_ENVS.get(tool)
    return env if env and env != "default" else None


def environment_tools(env: str) -> list[str]:
    """The optional tools the Pixi environment ``env`` provides."""
    return [tool for tool in OPTIONAL_TOOLS if PIXI_TOOL_ENVS.get(tool) == env]


def _managed_jar_path(tool: str) -> Path | None:
    """Path to a CLI-managed JAR in ``managed_jar_dir()``, or None if not managed."""
    if tool not in MANAGED_JAR_TOOLS:
        return None
    from wgsextract_cli.core.config import settings
    from wgsextract_cli.core.dependencies import (
        _which_tool,
        find_discvrseq_jar,
        managed_jar_dir,
    )

    if _which_tool(tool):
        return None
    jar = find_discvrseq_jar() if tool == "discvrseq" else None
    if not jar:
        return None
    jar_path = Path(jar)
    explicit = settings.get("discvrseq_jar") if tool == "discvrseq" else None
    if isinstance(explicit, str) and explicit.strip():
        if not _same_or_inside(jar_path, managed_jar_dir()):
            return None
    return jar_path if _same_or_inside(jar_path, managed_jar_dir()) else None


def installable(tool: str) -> bool:
    """Whether ``deps install`` can install ``tool``: either a managed JAR tool
    or a tool with a Pixi environment of its own that this installation can
    manage and whose lockfile doesn't rule this platform out."""
    if tool in MANAGED_JAR_TOOLS:
        return _unavailable_reason(tool) is None
    return (
        own_environment(tool) is not None
        and can_manage_environments()
        and locked_for_platform(tool) is not False
    )


def _host() -> str:
    return f"{sys.platform} {platform.machine()}"


def _unavailable_reason(tool: str) -> str | None:
    """Why ``tool`` can't be had on this computer, or None when it can, from
    its environment or from a release the user installs."""
    if tool in platform_unavailable_tools():
        return f"{tool} isn't built for {_host()}."
    reason = unsupported_windows_arm64_tool(tool) or tool_unsupported_reason(tool)
    if reason:
        return reason
    no_build = PIXI_TOOL_ENVS.get(tool) and locked_for_platform(tool) is False
    if no_build and tool not in INSTALL_HINTS:
        return f"No {tool} build is available for {_host()}."
    return None


def _same_or_inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except (OSError, RuntimeError):
        return False


def _runs_cli(env: str) -> bool:
    """Whether the CLI itself runs in ``env``: removing it would remove the
    CLI. Unless Pixi names the environment, the running Python's prefix is
    compared with the environment's."""
    if os.environ.get("PIXI_ENVIRONMENT_NAME") == env:
        return True
    prefix = pixi_env_prefix(env)
    return prefix is not None and _same_or_inside(Path(sys.prefix), prefix)


def _from_environment(path: str, env: str) -> bool:
    """Whether the tool found at ``path`` comes from the Pixi environment
    ``env``: a ``pixi run -e env`` launcher, or a file inside that prefix.
    A tool on PATH, or in another environment (``dev``), is not that
    environment's to remove."""
    if is_pixi_tool_command(path):
        try:
            argv = shlex.split(path, posix=not runtime.is_windows_host())
        except ValueError:
            return False
        return any(a == "-e" and b == env for a, b in zip(argv, argv[1:], strict=False))
    prefix = pixi_env_prefix(env)
    return prefix is not None and _same_or_inside(Path(path), prefix)


def tool_row(row: dict[str, Any], group: str) -> dict[str, Any]:
    """A ``check_all_dependencies`` row with what ``deps list`` adds: the
    tool's ``group``, ``status``, Pixi ``environment`` (or ``jartools`` for
    managed JARs), whether ``deps install`` / ``deps uninstall`` can act on it,
    and a ``note`` saying how to get it otherwise or why it can't be had."""
    tool = str(row["name"])
    jar_spec = MANAGED_JAR_TOOLS.get(tool) if group == "optional" else None
    env = own_environment(tool) if group == "optional" else None
    path = row.get("path")
    unavailable = None if path or group != "optional" else _unavailable_reason(tool)
    status = INSTALLED if path else UNAVAILABLE if unavailable else MISSING
    if jar_spec is not None:
        managed_jar = _managed_jar_path(tool) if path else None
        can_install = status == MISSING and installable(tool)
        can_uninstall = bool(path and managed_jar is not None)
        jar_env = "jartools" if (can_install or can_uninstall) else None
        note = unavailable
        if can_install:
            note = (
                f"Downloads {jar_spec.filename} ({jar_spec.size_label}); "
                "requires Java 17 (java17)."
            )
        elif status == MISSING:
            note = INSTALL_HINTS.get(tool)
        return row | {
            "group": group,
            "status": status,
            "environment": jar_env,
            "environment_tools": [tool] if jar_env else [],
            "can_install": can_install,
            "can_uninstall": can_uninstall,
            "note": note,
        }
    can_install = status == MISSING and env is not None and installable(tool)
    note = unavailable
    if status == MISSING and not can_install:
        note = INSTALL_HINTS.get(tool)
    elif can_install:
        note = PIXI_SETUP_HINTS.get(tool)
    can_uninstall = bool(
        path and env and _from_environment(str(path), env) and not _runs_cli(env)
    )
    return row | {
        "group": group,
        "status": status,
        "environment": env,
        "environment_tools": environment_tools(env) if env else [],
        "can_install": can_install,
        "can_uninstall": can_uninstall,
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
    from wgsextract_cli.core.network_policy import require_network
    from wgsextract_cli.core.utils import run_command

    if action == "install":
        require_network(f"Install Pixi environment {env}")
    argv = pixi_environment_argv(action, env)
    if argv is None:
        raise MissingDependencyError(
            "This installation has no Pixi, or no Pixi workspace of its own "
            "(pixi.toml), so the CLI can't manage its environments. Reinstall "
            "WGS Extract, or install the tool yourself."
        )
    run_command(argv)


def _install_managed_jar(tool: str, spec: ManagedJarTool) -> dict[str, Any]:
    from wgsextract_cli.core.dependencies import (
        clear_tool_caches,
        get_tool_path,
        managed_jar_dir,
    )
    from wgsextract_cli.core.ref_library.downloads import download_file

    java = get_tool_path("java17") or get_tool_path("java")
    if not java:
        raise MissingDependencyError(
            f"Java is required to run {tool}. Install java17 and retry."
        )
    dest_dir = managed_jar_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / spec.filename
    logging.info(f"Downloading {spec.filename} ({spec.size_label}) to {dest}...")
    ok = download_file(
        spec.url,
        str(dest),
        expected_sha256=spec.sha256,
        use_mirrors=False,
        resolve_release_digest=False,
    )
    if not ok or not dest.is_file():
        raise WGSExtractError(f"Failed to download {spec.filename} from {spec.url}.")

    clear_tool_caches()
    path = get_tool_path(tool)
    if not path:
        raise MissingDependencyError(
            f"Downloaded {dest}, but {tool} still isn't found."
        )
    return {
        "tool": tool,
        "environment": "jartools",
        "tools": [tool],
        "path": path,
        "jar_path": str(dest),
    }


def _uninstall_managed_jar(tool: str) -> dict[str, Any]:
    from wgsextract_cli.core.dependencies import clear_tool_caches

    jar_path = _managed_jar_path(tool)
    if jar_path is None:
        raise WGSExtractError(
            f"No WGS Extract-managed {tool} JAR is installed to remove."
        )
    logging.info(f"Removing {tool} JAR at {jar_path}...")
    jar_path.unlink()
    clear_tool_caches()
    return {
        "tool": tool,
        "environment": "jartools",
        "tools": [tool],
        "path": str(jar_path),
        "jar_path": str(jar_path),
    }


def install(tool: str) -> dict[str, Any]:
    """Download and install the optional tool (managed JAR or Pixi environment)."""
    if spec := MANAGED_JAR_TOOLS.get(tool):
        if reason := _unavailable_reason(tool):
            raise WGSExtractError(reason)
        return _install_managed_jar(tool, spec)
    env = _environment_for(tool)
    if reason := _unavailable_reason(tool):
        raise WGSExtractError(reason)
    if locked_for_platform(tool) is False:
        raise WGSExtractError(
            f"No {tool} build is available for {_host()}. {INSTALL_HINTS[tool]}"
        )
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
    """Remove a managed JAR or the Pixi environment that provides ``tool``."""
    if tool in MANAGED_JAR_TOOLS:
        return _uninstall_managed_jar(tool)
    env = _environment_for(tool)
    if _runs_cli(env):
        raise WGSExtractError(
            f"The WGS Extract CLI runs in the Pixi environment '{env}'; it "
            f"can't remove {tool} from it."
        )
    if pixi_env_prefix(env) is None:
        raise WGSExtractError(
            f"The Pixi environment '{env}' that provides {tool} isn't installed."
        )
    tools = environment_tools(env)
    logging.info(
        f"Removing the Pixi environment '{env}' (provides {', '.join(tools)})..."
    )
    _run_pixi("clean", env)

    from wgsextract_cli.core.dependencies import clear_tool_caches

    clear_tool_caches()
    return {"tool": tool, "environment": env, "tools": tools}


# Options whose value names the tool a run uses (--caller sniffles,
# --phaser longphase, --aligner minimap2). Other values, such as paths, never
# select a tool, whatever they are called.
TOOL_OPTIONS = ("caller", "phaser", "aligner")

# A tool COMMAND_NEEDS names differently from the catalog.
TOOL_ALIASES = {"pharmcat": "pharmcat_python"}

# Tools whose executable the user may name (an option, or its config.toml
# setting); the command checks that executable itself.
EXECUTABLE_OVERRIDES = {
    "dorado": ("dorado_path", "dorado_executable"),
    "haplogrep3": ("haplogrep_path", "haplogrep_executable"),
    "java17": (None, "java17_executable"),
    "discvrseq": ("discvrseq_jar", "discvrseq_jar"),
    "granite": ("granite_path", "granite_executable"),
}


def _vep_tools(args: argparse.Namespace, *, download: bool) -> list[str]:
    """The engine's tools when ``--engine`` or config ``vep_engine`` names
    one; otherwise ``vep`` picks it from the caches it finds, and reports
    a missing tool itself."""
    from wgsextract_cli.commands._vep_engines import ENGINES
    from wgsextract_cli.core.config import settings

    engine = ENGINES.get(
        str(getattr(args, "engine", None) or settings.get("vep_engine") or "")
    )
    if engine is None:
        return []
    return list(engine.cache_tools) if download else [engine.tool]


def _phase_tools(args: argparse.Namespace) -> list[str]:
    """Without --phaser, WhatsHap unless LongPhase is installed (which it
    uses for long reads): as ``vcf phase`` chooses."""
    from wgsextract_cli.core.dependencies import get_tool_path

    if getattr(args, "phaser", None) or get_tool_path("longphase"):
        return []
    return ["whatshap"]


def _impute_tools(args: argparse.Namespace) -> list[str]:
    """Imputing from reads (--input) phases and ligates with GLIMPSE2."""
    if getattr(args, "input", None):
        return ["GLIMPSE2_phase", "GLIMPSE2_ligate"]
    return []


def _gvcf_tools(args: argparse.Namespace) -> list[str]:
    """DeepVariant writes its gVCF reference evidence with bcftools."""
    return ["bcftools"] if getattr(args, "reference_evidence", None) == "gvcf" else []


# Tools a command needs beyond COMMAND_NEEDS, chosen as the command chooses.
# vep run and vep download replace the default engine's tools.
_COMMAND_TOOLS: dict[str, Callable[[argparse.Namespace], list[str]]] = {
    "vep run": lambda args: _vep_tools(args, download=False),
    "vep download": lambda args: _vep_tools(args, download=True),
    "vcf phase": _phase_tools,
    "vcf impute": _impute_tools,
    "vcf deepvariant": _gvcf_tools,
    "vcf variantqc": lambda _args: ["java17", "discvrseq"],
    "vcf granite-qc": lambda _args: ["granite"],
}
_VEP_TOOLS = frozenset({"vep", "vep-rs", "vep-cache-builder"})


def _chosen(args: argparse.Namespace, optional: Iterable[str]) -> list[str]:
    """The optional tools the command line selects: a tool option's value
    (case aside: --caller expansionhunter is ExpansionHunter), or a flag
    named after the tool (--multiqc, --plink2-check)."""
    by_name = {tool.lower(): tool for tool in optional}
    chosen = []
    for name, value in vars(args).items():
        if name in TOOL_OPTIONS and isinstance(value, str):
            chosen.append(by_name.get(value.lower()))
        elif value is True:
            chosen.append(by_name.get(name.split("_")[0].lower()))
    return [tool for tool in chosen if tool]


def executable_override(tool: str, args: argparse.Namespace) -> str | None:
    """The executable the user named for ``tool`` (its option, or its
    config.toml setting), which the command checks itself; None when unnamed."""
    from wgsextract_cli.core.config import settings

    option, setting = EXECUTABLE_OVERRIDES.get(tool, (None, None))
    named = (option and getattr(args, option, None)) or (
        setting and settings.get(setting)
    )
    return str(named) if named else None


def command_tools(args: argparse.Namespace) -> list[str]:
    """The external tools the parsed command line ``args`` runs, by catalog
    name: those every run of the command needs (``COMMAND_NEEDS``), those an
    option selects (``--caller sniffles``, ``--plink2-check``), and those the
    command picks by its own rules (``vcf phase``'s default phaser, ``vep
    run``'s engine)."""
    from wgsextract_cli.commands._command_needs import COMMAND_NEEDS

    command = str(getattr(args, "_command", "") or "")
    needs = COMMAND_NEEDS.get(command)
    if needs is None or command.split()[0] in {"deps", "help", "serve"}:
        return []
    if getattr(args, "delete", False) is True:
        return []  # `ref imputation --delete` removes files; it runs no tools.
    wanted = [*needs.tools, *_chosen(args, needs.optional)]
    if command in ("vep run", "vep download"):
        wanted = [tool for tool in wanted if tool not in _VEP_TOOLS]
    if resolver := _COMMAND_TOOLS.get(command):
        wanted += resolver(args)
    return list(dict.fromkeys(TOOL_ALIASES.get(t, t) for t in wanted))


def missing_tools(args: argparse.Namespace) -> list[str]:
    """The optional tools of ``command_tools`` that aren't installed. Tools
    this platform can't have, and tools whose executable the user named, are
    left to the command."""
    from wgsextract_cli.core.dependencies import get_tool_path

    unavailable = set(platform_unavailable_tools())
    return [
        tool
        for tool in command_tools(args)
        if tool in OPTIONAL_TOOLS
        and tool not in unavailable
        and not executable_override(tool, args)
        and not get_tool_path(tool)
        and not _unavailable_reason(tool)
    ]
