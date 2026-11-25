from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.genotyping import (
    TypingCyp2d6Params,
    TypingHlaParams,
    TypingParaphaseParams,
    TypingSmnParams,
    TypingStrParams,
    TypingTargetCnvParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._command_needs import (
    MINUTES,
    SECONDS,
    _n,
)
from wgsextract_cli.commands._messages.genotyping import CLI_HELP

NEEDS = {
    "typing cyp2d6": _n(MINUTES, "cyrius", "minimap2", "samtools", optional="bcftools"),
    "typing paraphase": _n(MINUTES, "samtools", optional="paraphase minimap2 bcftools"),
    "typing smn": _n(MINUTES, "smn_caller.py", "samtools", optional="bcftools"),
    "typing target-cnv": _n(SECONDS, "samtools", optional="bcftools curl"),
    "typing hla": _n(
        MINUTES, "run-t1k", "samtools", "t1k-build.pl", optional="bcftools"
    ),
    "typing str": _n(
        MINUTES,
        "samtools",
        optional="ExpansionHunter LongTR strkit bcftools",
    ),
}


cmd_cyp2d6 = command_handler("wgsextract_cli.commands.genotyping.cyp2d6", "cmd_cyp2d6")
cmd_hla = command_handler("wgsextract_cli.commands.genotyping.hla", "cmd_hla")
cmd_paraphase = command_handler(
    "wgsextract_cli.commands.genotyping.paraphase", "cmd_paraphase"
)
cmd_smn = command_handler("wgsextract_cli.commands.genotyping.smn", "cmd_smn")
cmd_str = command_handler("wgsextract_cli.commands.genotyping.repeats", "cmd_str")
cmd_target_cnv = command_handler(
    "wgsextract_cli.commands.genotyping.target_cnv", "cmd_target_cnv"
)


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
    paraphase = subs.add_parser(
        "paraphase", parents=[base_parser], help=CLI_HELP["cmd_typing-paraphase"]
    )
    add_arguments(paraphase, TypingParaphaseParams)
    paraphase.set_defaults(func=cmd_paraphase)
    smn = subs.add_parser("smn", parents=[base_parser], help=CLI_HELP["cmd_typing-smn"])
    add_arguments(smn, TypingSmnParams)
    smn.set_defaults(func=cmd_smn)
    repeats = subs.add_parser(
        "str", parents=[base_parser], help=CLI_HELP["cmd_typing-str"]
    )
    add_arguments(repeats, TypingStrParams)
    repeats.set_defaults(func=cmd_str)
    target_cnv = subs.add_parser(
        "target-cnv", parents=[base_parser], help=CLI_HELP["cmd_typing-target-cnv"]
    )
    add_arguments(target_cnv, TypingTargetCnvParams)
    target_cnv.set_defaults(func=cmd_target_cnv)
