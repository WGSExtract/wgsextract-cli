from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.pet import PetAlignParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "pet-align", parents=[base_parser], help=CLI_HELP["cmd_pet-align"]
    )
    add_arguments(parser, PetAlignParams)
    parser.set_defaults(
        func=command_handler("wgsextract_cli.commands.pet", "run_pet", PetAlignParams)
    )
