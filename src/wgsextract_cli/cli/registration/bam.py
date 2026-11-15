from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.bam import (
    BamIdentifyParams,
    BamIndexParams,
    BamMergeParams,
    BamSortParams,
    BamToBamParams,
    BamToCramParams,
    BamToUbamParams,
    BamUnalignParams,
    BamUnindexParams,
    BamUnsortParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP

cmd_identify = command_handler("wgsextract_cli.commands.bam", "cmd_identify")
cmd_index = command_handler("wgsextract_cli.commands.bam", "cmd_index")
cmd_merge = command_handler("wgsextract_cli.commands.bam", "cmd_merge")
cmd_sort = command_handler("wgsextract_cli.commands.bam", "cmd_sort")
cmd_to_ubam = command_handler("wgsextract_cli.commands._bam_ubam", "cmd_to_ubam")
cmd_tobam = command_handler("wgsextract_cli.commands.bam", "cmd_tobam")
cmd_tocram = command_handler("wgsextract_cli.commands.bam", "cmd_tocram")
cmd_unalign = command_handler("wgsextract_cli.commands.bam", "cmd_unalign")
cmd_unindex = command_handler("wgsextract_cli.commands.bam", "cmd_unindex")
cmd_unsort = command_handler("wgsextract_cli.commands.bam", "cmd_unsort")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser("bam", help=CLI_HELP["cmd_bam_mgmt"])
    bam_subs = parser.add_subparsers(dest="bam_cmd", required=True)
    for name, cls, func, help_key in (
        ("sort", BamSortParams, cmd_sort, "cmd_sort"),
        ("index", BamIndexParams, cmd_index, "cmd_index"),
        ("unindex", BamUnindexParams, cmd_unindex, "cmd_unindex"),
        ("unsort", BamUnsortParams, cmd_unsort, "cmd_unsort"),
        ("to-cram", BamToCramParams, cmd_tocram, "cmd_to-cram"),
        ("to-bam", BamToBamParams, cmd_tobam, "cmd_to-bam"),
        ("unalign", BamUnalignParams, cmd_unalign, "cmd_unalign"),
        ("identify", BamIdentifyParams, cmd_identify, "cmd_bam-identify"),
        ("merge", BamMergeParams, cmd_merge, "cmd_bam-merge"),
        ("to-ubam", BamToUbamParams, cmd_to_ubam, "cmd_bam-to-ubam"),
    ):
        sub = bam_subs.add_parser(name, parents=[base_parser], help=CLI_HELP[help_key])
        add_arguments(sub, cls)
        sub.set_defaults(func=func)
