from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.lineage import (
    MtHaplogroupParams,
    YHaplogroupParams,
    YPrivateParams,
    YStrParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, experimental, tested
from wgsextract_cli.commands._command_needs import (
    _SAM,
    MINUTES,
    _n,
)
from wgsextract_cli.commands._messages.lineage import CLI_HELP

_REVIEWED = "2026-11-25"
CERTIFICATION = {
    "lineage y-haplogroup": tested(
        _REVIEWED,
        "test_lineage_chm13_parity.py",
        "Fixture lineage results compared across reference builds.",
    ),
    "lineage y-str": tested(
        _REVIEWED, "test_ystr.py", "Fixture Y-STR outputs and no-call cases."
    ),
    "lineage mt-haplogroup": tested(
        _REVIEWED,
        "test_lineage_haplogroups.py",
        "Fixture mitochondrial input and lineage checks.",
    ),
    "lineage y-private": experimental(
        _REVIEWED,
        "Private Y variants are research evidence, not confirmed novel lineage markers.",
        f"{REPO}/blob/main/tests/test_y_private.py",
    ),
}

NEEDS = {
    "lineage y-haplogroup": _n(MINUTES, "bcftools", "samtools"),
    "lineage y-private": _n(MINUTES, "bcftools", "bgzip", "samtools", "tabix"),
    "lineage y-str": _n(MINUTES, *_SAM, optional="bcftools"),
    "lineage mt-haplogroup": _n(
        MINUTES,
        "bcftools",
        "haplogrep3",
        "java",
        "samtools",
        optional="curl haplocheck tabix",
    ),
}


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
    private_parser = lin_subs.add_parser(
        "y-private", parents=[base_parser], help=CLI_HELP["cmd_lineage-y-private"]
    )
    add_arguments(private_parser, YPrivateParams)
    private_parser.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands._lineage_yprivate", "cmd_y_private"
        )
    )
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
