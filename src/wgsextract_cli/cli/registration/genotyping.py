from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.genotyping import (
    TypingCyp2d6Params,
    TypingHlaParams,
    TypingSmnParams,
    TypingStrParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP

cmd_cyp2d6 = command_handler("wgsextract_cli.commands.genotyping.cyp2d6", "cmd_cyp2d6")
cmd_hla = command_handler("wgsextract_cli.commands.genotyping.hla", "cmd_hla")
cmd_smn = command_handler("wgsextract_cli.commands.genotyping.smn", "cmd_smn")
cmd_str = command_handler("wgsextract_cli.commands.genotyping.repeats", "cmd_str")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser("typing", help=CLI_HELP["cmd_typing"])
    subs = parser.add_subparsers(dest="typing_cmd", required=True)
    cyp2d6 = subs.add_parser(
        "cyp2d6", parents=[base_parser], help=CLI_HELP["cmd_typing-cyp2d6"]
    )
    add_arguments(cyp2d6, TypingCyp2d6Params)
    cyp2d6.set_defaults(func=cmd_cyp2d6)
    hla = subs.add_parser("hla", parents=[base_parser], help=CLI_HELP["cmd_typing-hla"])
    add_arguments(hla, TypingHlaParams)
    hla.set_defaults(func=cmd_hla)
    smn = subs.add_parser("smn", parents=[base_parser], help=CLI_HELP["cmd_typing-smn"])
    add_arguments(smn, TypingSmnParams)
    smn.set_defaults(func=cmd_smn)
    repeats = subs.add_parser(
        "str", parents=[base_parser], help=CLI_HELP["cmd_typing-str"]
    )
    add_arguments(repeats, TypingStrParams)
    repeats.set_defaults(func=cmd_str)
