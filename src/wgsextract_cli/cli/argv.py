"""Recognize startup arguments before constructing the command parser."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from wgsextract_cli import __version__

if TYPE_CHECKING:
    from argparse import ArgumentParser


def register_version(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--version", action="version", version=f"wgsextract {__version__}"
    )


def show_version() -> None:
    from argparse import ArgumentParser

    parser = ArgumentParser(prog="wgsextract", add_help=False)
    register_version(parser)
    parser.parse_args(["--version"])


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
