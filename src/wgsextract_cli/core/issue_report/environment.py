"""Collect installation, tool and settings diagnostics for issue reports."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

from wgsextract_cli import __version__

from .redaction import Redactor


def _gib(n: float) -> str:
    return f"{n / 1024**3:.1f} GiB"


def _cpu_model() -> str:
    try:
        if sys.platform.startswith("linux"):
            for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
                if line.lower().startswith(("model name", "hardware", "cpu model")):
                    return line.split(":", 1)[1].strip()
        elif sys.platform == "darwin":
            completed = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                check=False,
            )
            if completed.stdout.strip():
                return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.processor() or "unknown"


def _install_type() -> str:
    from wgsextract_cli.core.runtime import source_checkout_root

    root = source_checkout_root()
    if root is not None:
        try:
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            commit = ""
        return f"source checkout, commit {commit}" if commit else "source checkout"
    prefix = Path(sys.prefix).parts
    if ".pixi" in prefix:
        return "Pixi environment (installer)"
    if "conda" in sys.version.lower() or Path(sys.prefix, "conda-meta").is_dir():
        return "conda environment"
    return "Python package"


def _os_text() -> str:
    system = platform.system()
    if system == "Darwin":
        text = f"macOS {platform.mac_ver()[0]}"
    elif system == "Windows":
        text = f"Windows {platform.release()} ({platform.version()})"
    else:
        text = f"{system} {platform.release()}"
        if "microsoft" in platform.release().lower():
            text += " (WSL)"
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                if line.startswith("PRETTY_NAME="):
                    text += f", {line.split('=', 1)[1].strip(chr(34))}"
        except OSError:
            pass
    return f"{text}, {platform.machine()}"


def environment_lines() -> list[str]:
    """This installation and computer, without names or paths."""
    import psutil

    memory = psutil.virtual_memory()
    logical = psutil.cpu_count() or os.cpu_count() or 0
    physical = psutil.cpu_count(logical=False)
    cores = f"{logical} threads" + (f", {physical} cores" if physical else "")
    lines = [
        f"- wgsextract: {__version__} ({_install_type()})",
        f"- OS: {_os_text()}",
        f"- Python: {platform.python_version()} ({platform.python_implementation()})",
        f"- CPU: {_cpu_model()}, {cores}",
        f"- RAM: {_gib(memory.total)} total, {_gib(memory.available)} available",
    ]
    from wgsextract_cli.core.config import settings

    places = {"current folder": Path.cwd()}
    for key, label in (
        ("output_directory", "output_directory"),
        ("temp_directory", "temp_directory"),
    ):
        if settings.get(key):
            places[label] = Path(str(settings[key])).expanduser()
    free = []
    for label, target in places.items():
        while not target.exists() and target != target.parent:
            target = target.parent
        try:
            free.append(f"{_gib(shutil.disk_usage(target).free)} ({label})")
        except OSError:
            continue
    if free:
        lines.append("- Free disk: " + ", ".join(free))
    return lines


def tool_versions(extra: Iterable[str] = ()) -> dict[str, str]:
    """Versions of the mandatory tools and ``extra`` (those a run used)."""
    from wgsextract_cli.core.results import tool_version
    from wgsextract_cli.core.tool_catalog import required_dependency_tools

    versions: dict[str, str] = {}
    for tool in dict.fromkeys([*required_dependency_tools(False), *extra]):
        version = tool_version(tool)
        versions[tool] = version or "not found"
    return versions


def settings_lines(redactor: Redactor) -> list[str]:
    """The config.toml settings that are set, redacted; secrets never shown."""
    from wgsextract_cli.core.config import get_config_path, settings
    from wgsextract_cli.core.results import _SECRET_NAME
    from wgsextract_cli.core.settings_schema import SETTINGS

    lines = []
    for key in sorted(settings):
        value = settings[key]
        known = SETTINGS.get(key)
        if value is None or (known is not None and known.default == value):
            continue
        if _SECRET_NAME.search(key) or known is None:
            shown = "<set>"
        elif isinstance(value, str):
            shown = redactor.text(value)
        else:
            shown = redactor.text(json.dumps(value))
        lines.append(f"- {key} = {shown}")
    exists = get_config_path().is_file()
    head = "config.toml" if exists else "no config.toml (defaults)"
    return [f"- {head}; settings that differ from the defaults:"] + (
        [f"  {line}" for line in lines] if lines else ["  - none"]
    )
