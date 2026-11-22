"""Recognize launch failures reported by Windows App Control."""

import re
import shutil
from pathlib import Path

_POLICY_PREFIX = "Windows Application Control blocked "
_CHILD_BLOCK = re.compile(
    r"""^(?:bwa-mem2: can't start (.+) \(error 4551\)|"""
    r"""Error launching '([^'\r\n]+)': .+ \(os error 4551\))$""",
    re.MULTILINE,
)


def _message(executable: str) -> str:
    return (
        f"{_POLICY_PREFIX}{executable} (Smart App Control or App Control for "
        "Business/WDAC). Use a signed build allowed by your policy or ask "
        "your administrator for an approved build."
    )


def blocked_launch(executable: str, error: OSError) -> str | None:
    if getattr(error, "winerror", None) == 4551:
        return _message(shutil.which(executable) or executable)
    return None


def launch_error_message(executable: str, error: OSError) -> str:
    return blocked_launch(executable, error) or (
        f"Could not start {Path(executable).name}: {error.strerror or error}. "
        "Run 'wgsextract deps check' to validate installed tools."
    )


def blocked_child(stderr: str) -> str | None:
    """The BWA-MEM2 dispatcher and Pixi report a child's CreateProcess error."""
    match = _CHILD_BLOCK.search(stderr)
    return _message(match[1] or match[2]) if match else None


def is_blocked_probe(version: str | None) -> bool:
    return bool(version and version.startswith(f"Error: {_POLICY_PREFIX}"))
