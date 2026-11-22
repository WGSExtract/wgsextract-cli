import logging
import platform
import sys
from typing import Any

from wgsextract_cli.api.deps import (
    DepsCheckParams,
    DepsInstallParams,
    DepsListParams,
    DepsReadyParams,
    DepsUninstallParams,
)
from wgsextract_cli.core import optional_tools
from wgsextract_cli.core.dependency_checks import check_all_dependencies
from wgsextract_cli.core.native_support import require_native_tool
from wgsextract_cli.core.pixi_tools import pixi_install_command
from wgsextract_cli.core.results import current_result, human_output
from wgsextract_cli.core.tool_catalog import platform_unavailable_tools
from wgsextract_cli.core.utils import MissingDependencyError
from wgsextract_cli.core.windows_app_control import is_blocked_probe


def _stdout_can_encode(*values: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return True
    try:
        for value in values:
            value.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def _status_text(path: object, *, optional: bool = False, alt_env: bool = False) -> str:
    present = bool(path)
    if _stdout_can_encode("✅", "❌", "⚠️", "🔵"):
        if present and alt_env:
            return "🔵"
        if present:
            return "✅"
        return "⚠️ " if optional else "❌"
    if present and alt_env:
        return "ALT"
    if present:
        return "OK"
    return "WARN" if optional else "MISS"


def _tool_source(tool: dict[str, Any]) -> str:
    if is_blocked_probe(tool["version"]):
        return " [blocked]"
    alt_env = tool.get("alt_env")
    if alt_env:
        return f" [pixi alt env: {alt_env}]"
    return f" [{tool['runtime']}]" if tool.get("runtime") else ""


def _row(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        key: tool.get(key) for key in ("name", "path", "runtime", "version", "alt_env")
    }


def run(params: DepsCheckParams, *, debug: bool = False) -> None:
    """``deps check``. ``debug`` (the CLI's --debug) logs one tool's path
    instead of printing it."""
    result = current_result()
    if params.tool:
        require_native_tool(params.tool)
        from wgsextract_cli.core.dependencies import get_tool_path

        path = get_tool_path(params.tool)
        result.set_details({"tool": params.tool, "path": path})
        if path:
            if not debug:
                human_output(path)
            else:
                logging.debug(f"Found {params.tool} at {path}")
            return
        else:
            command = pixi_install_command(params.tool)
            hint = f". Run: {command}" if command else ""
            raise MissingDependencyError(f"Tool not found: {params.tool}{hint}")

    results = _report()
    missing, blocked = [], []
    for tool in results["mandatory"]:
        if is_blocked_probe(tool["version"]):
            blocked.append(tool["version"].removeprefix("Error: "))
        elif not tool["path"]:
            missing.append(tool["name"])
    messages = []
    if missing:
        messages.append(
            f"Missing mandatory tool(s): {', '.join(missing)}. "
            "Install them to ensure full functionality."
        )
    if messages or blocked:
        raise MissingDependencyError(" ".join(messages + blocked))
    logging.info("All mandatory tools verified successfully.")


def run_list(params: DepsListParams) -> None:
    """``deps list``: the report ``deps check`` prints, without failing when a
    mandatory tool is missing."""
    _report()


def run_ready(params: DepsReadyParams) -> None:
    """``deps ready``: each prerequisite of a command line and whether it
    can run here offline; fails (exit 3) when it can't."""
    import shlex

    from wgsextract_cli.core.readiness import inspect_command

    argv = list(params.command_line)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    report = inspect_command(argv)
    details = report.as_dict()
    current_result().set_details(details)
    human_output(f"Readiness: wgsextract {shlex.join(details['command_line'])}")
    host = details["host"]
    human_output(f"Host: {host['os']} {host['machine']}\n")
    for item in details["items"]:
        version = f" [{item['version']}]" if item["version"] else ""
        note = "" if item["required"] else " (advisory)"
        human_output(
            f"{item['status']:<12} {item['kind']:<10} {item['name']}{version}{note}"
        )
        if item["detail"]:
            human_output(f"{'':<24}{item['detail']}")
        licence = (item["evidence"].get("terms") or {}).get("licence")
        if licence:
            human_output(f"{'':<24}licence: {licence}")
        if item["status"] in ("missing", "stale", "incompatible") and (
            item["fix_arguments"] or item["fix_text"]
        ):
            fix = (
                f"wgsextract {shlex.join(item['fix_arguments'])}"
                if item["fix_arguments"]
                else item["fix_text"]
            )
            human_output(f"{'':<24}fix: {fix}")
    human_output("")
    for notice in details["notices"]:
        human_output(notice)
    if details["ready"]:
        human_output(
            "Ready to run offline"
            + (
                " (some items are unverified)."
                if details["status"] == "ready_unverified"
                else "."
            )
        )
        return
    raise MissingDependencyError(
        "Not ready to run offline; blocked by: " + ", ".join(details["blocking"])
    )


def run_install(params: DepsInstallParams) -> None:
    """``deps install``."""
    details = optional_tools.install(params.tool)
    current_result().set_details(details)
    human_output(f"Installed {params.tool} ({details['path']}).")


def run_uninstall(params: DepsUninstallParams) -> None:
    """``deps uninstall``."""
    details = optional_tools.uninstall(params.tool)
    current_result().set_details(details)
    human_output(
        f"Removed the Pixi environment '{details['environment']}' "
        f"({', '.join(details['tools'])})."
    )


def _report() -> dict[str, list[dict[str, Any]]]:
    """Checks every tool, prints the table and sets the result's details:
    ``mandatory`` and ``optional`` rows, ``unavailable_on_platform`` and
    ``tools``, every tool with its status and what can be done about it."""
    logging.info("Verifying bioinformatics tool installations...")
    results = check_all_dependencies()
    unavailable = platform_unavailable_tools()
    tools = [
        optional_tools.tool_row(_row(tool), group)
        for group in ("mandatory", "optional")
        for tool in results[group]
    ] + [optional_tools.unavailable_row(tool) for tool in unavailable]
    current_result().set_details(
        {group: [_row(tool) for tool in results[group]] for group in results}
        | {"unavailable_on_platform": unavailable, "tools": tools}
    )

    human_output("\nMandatory Tools:")
    human_output("-" * 60)
    for tool in results["mandatory"]:
        status = _status_text(tool["path"], alt_env=bool(tool.get("alt_env")))
        source = _tool_source(tool)
        version = f" ({tool['version']})" if tool["version"] else ""
        human_output(f"{status} {tool['name']:<20} {source}{version}")

    human_output("\nOptional Tools:")
    human_output("-" * 60)
    for tool in results["optional"]:
        status = _status_text(
            tool["path"], optional=True, alt_env=bool(tool.get("alt_env"))
        )
        source = _tool_source(tool)
        version = f" ({tool['version']})" if tool["version"] else ""
        human_output(f"{status} {tool['name']:<20} {source}{version}")

    if unavailable:
        host = f"{sys.platform} {platform.machine()}"
        human_output(f"\nNot available on {host}: {', '.join(unavailable)}")

    human_output("\n" + "=" * 60)
    return results
