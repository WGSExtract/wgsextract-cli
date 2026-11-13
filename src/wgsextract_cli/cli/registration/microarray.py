from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.microarray import MicroarrayParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP

run = command_handler("wgsextract_cli.commands.microarray", "run")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "microarray",
        parents=[base_parser],
        help=CLI_HELP["cmd_microarray"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    add_arguments(parser, MicroarrayParams)
    parser.set_defaults(func=run)
