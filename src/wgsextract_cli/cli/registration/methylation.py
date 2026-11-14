from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.methylation import MethylationPileupParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser("methylation", help=CLI_HELP["cmd_methylation"])
    subs = parser.add_subparsers(dest="methylation_cmd", required=True)
    pileup = subs.add_parser(
        "pileup", parents=[base_parser], help=CLI_HELP["cmd_methylation-pileup"]
    )
    add_arguments(pileup, MethylationPileupParams)
    pileup.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands.methylation", "run_pileup", MethylationPileupParams
        )
    )
