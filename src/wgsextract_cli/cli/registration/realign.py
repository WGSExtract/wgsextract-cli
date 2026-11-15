from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.align import RealignParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "realign", parents=[base_parser], help=CLI_HELP["cmd_realign"]
    )
    add_arguments(parser, RealignParams)
    parser.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands.realign", "run_realign", RealignParams
        )
    )
