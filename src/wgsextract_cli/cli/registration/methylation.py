from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.methylation import (
    MethylationPileupParams,
    MethylationRegionsParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, tested, validated
from wgsextract_cli.commands._command_needs import (
    HOURS,
    MINUTES,
    _n,
)
from wgsextract_cli.commands._messages.methylation import CLI_HELP

_REVIEWED = "2026-11-25"
CERTIFICATION = {
    "methylation pileup": tested(
        _REVIEWED,
        "test_methylation.py",
        "modkit output and tag handling checked on fixture reads.",
    ),
    "methylation regions": validated(
        _REVIEWED,
        "HG002 promoter/island BGZF–Parquet parity and chr21 HP checks; not whole-genome read-level phasing validation.",
        ("macOS",),
        f"{REPO}/issues/1293#issuecomment-7169747432",
        f"{REPO}/blob/main/tests/test_methylation_regions.py",
    ),
}

NEEDS = {
    "methylation pileup": _n(HOURS, "modkit", "samtools", "tabix", optional="bcftools"),
    "methylation regions": _n(MINUTES, "samtools", optional="modkit curl"),
}


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
    regions = subs.add_parser(
        "regions", parents=[base_parser], help=CLI_HELP["cmd_methylation-regions"]
    )
    add_arguments(regions, MethylationRegionsParams)
    regions.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands.methylation_regions",
            "run_regions",
            MethylationRegionsParams,
        )
    )
