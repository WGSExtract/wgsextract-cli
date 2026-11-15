from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.deps import DepsCheckParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    deps_parser = subparsers.add_parser("deps", help=CLI_HELP["cmd_deps"])
    deps_subparsers = deps_parser.add_subparsers(dest="subcommand", required=True)
    check_parser = deps_subparsers.add_parser(
        "check", parents=[base_parser], help=CLI_HELP["cmd_check-deps"]
    )
    add_arguments(check_parser, DepsCheckParams)
    check_parser.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands._deps_status",
            "run",
            DepsCheckParams,
            debug="debug",
        )
    )
