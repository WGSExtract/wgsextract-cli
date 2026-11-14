from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.lineage import MtHaplogroupParams, YHaplogroupParams, YStrParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP

cmd_mtdna = command_handler("wgsextract_cli.commands.lineage", "cmd_mtdna")
cmd_ydna = command_handler("wgsextract_cli.commands._lineage_ydna", "cmd_ydna")


def add_ystr_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
    help_text: str,
) -> None:
    parser = subparsers.add_parser("y-str", parents=[base_parser], help=help_text)
    add_arguments(parser, YStrParams)
    parser.set_defaults(
        func=command_handler("wgsextract_cli.commands._lineage_ystr", "cmd_ystr")
    )


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "lineage", help="Native Yleaf or Haplogrep 3 analysis."
    )
    lin_subs = parser.add_subparsers(dest="lin_cmd", required=True)
    ydna_parser = lin_subs.add_parser(
        "y-haplogroup", parents=[base_parser], help=CLI_HELP["cmd_lineage-y-haplogroup"]
    )
    add_arguments(ydna_parser, YHaplogroupParams)
    ydna_parser.set_defaults(func=cmd_ydna)
    ydna_parser.add_argument(
        "--tree-links", action="store_true", help="Print YFull and FTDNA tree links."
    )
    add_ystr_parser(lin_subs, base_parser, CLI_HELP["cmd_lineage-y-str"])
    mtdna_parser = lin_subs.add_parser(
        "mt-haplogroup",
        parents=[base_parser],
        help=CLI_HELP["cmd_lineage-mt-haplogroup"],
    )
    add_arguments(mtdna_parser, MtHaplogroupParams)
    mtdna_parser.set_defaults(func=cmd_mtdna)
    mtdna_parser.add_argument(
        "--tree-links",
        action="store_true",
        help="Print YFull and FamilyTreeDNA mtDNA tree links.",
    )
