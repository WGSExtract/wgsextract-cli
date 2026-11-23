"""Which external tools a command line runs, and their installed versions,
for result manifests and step journals."""

from __future__ import annotations

import platform as _platform
import re
from collections.abc import Sequence
from pathlib import Path

_VERSION = re.compile(r"\d+(?:\.\d+)+[\w.+-]*")
# Launchers whose own version says nothing about the tool they run.
_LAUNCHERS = {"pixi", "env", "wsl", "conda", "mamba", "micromamba", "nice", "ionice"}


def tools_in_command(command: Sequence[str]) -> set[str]:
    """Names of the external tools a command line runs (best effort)."""
    tools: set[str] = set()
    parts = list(command)
    # Skip launchers such as `pixi run -e env --manifest-path X tool ...`.
    while parts and Path(parts[0]).stem.lower() in _LAUNCHERS:
        parts.pop(0)
        if parts and parts[0] == "run":
            parts.pop(0)
        while parts and parts[0].startswith("-"):
            option = parts.pop(0)
            if "=" not in option and option in {
                "-e",
                "--environment",
                "--manifest-path",
                "-n",
            }:
                parts = parts[1:]
    if parts:
        tools.add(Path(parts[0]).stem)
    tools.update(
        tool
        for tool in (
            "bwa",
            "bwa-mem2",
            "samtools",
            "bcftools",
            "gatk",
            "minimap2",
            "samblaster",
        )
        if tool in command
    )
    if "org.broadinstitute.hellbender.Main" in command:
        tools.add("gatk")
    return {tool for tool in tools if tool}


def tool_version(tool: str) -> str | None:
    """The version number of an installed tool, or None when unknown."""
    if tool.lower().startswith("python"):
        return _platform.python_version()
    from wgsextract_cli.core.dependency_checks import get_tool_version

    try:
        text = get_tool_version(tool)
    except Exception:  # a version probe must never fail a run
        return None
    if not text or text.startswith("Error"):
        return None
    match = _VERSION.search(text)
    return match.group(0) if match else text.strip()[:80]
