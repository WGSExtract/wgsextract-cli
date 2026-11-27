"""Recognize startup arguments before constructing the command parser."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from wgsextract_cli import __version__

if TYPE_CHECKING:
    from argparse import ArgumentParser

_VERSION_TEXT = f"wgsextract {__version__}"


def register_version(parser: ArgumentParser) -> None:
    parser.add_argument("--version", action="version", version=_VERSION_TEXT)


def show_version() -> None:
    try:
        (sys.stdout or sys.stderr).write(_VERSION_TEXT + "\n")
    except (AttributeError, OSError):
        # Match argparse when stdout is unavailable or the pipe closes.
        pass
    raise SystemExit(0)


def version_only(argv: Sequence[str]) -> bool:
    # Match the plain query and the config prefix supplied by the desktop
    # launcher. Leave abbreviations, other options and errors to argparse.
    match argv:
        case ["--version"]:
            return True
        case [option, "--version"]:
            return option.startswith("--config=")
        case ["--config", path, "--version"]:
            return not path.startswith("-")
        case _:
            return False


def config_path_from_argv(argv: Sequence[str]) -> str | None:
    """Read the config path before argparse loads its defaults."""
    found = None
    for index, token in enumerate(argv):
        if token == "--":
            break
        if token == "--config" and index + 1 < len(argv):
            found = argv[index + 1]
        elif token.startswith("--config="):
            found = token.split("=", 1)[1]
    return found
