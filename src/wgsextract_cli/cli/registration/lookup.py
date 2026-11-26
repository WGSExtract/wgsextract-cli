from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.lookup import LookupParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import tested
from wgsextract_cli.commands._command_needs import (
    SECONDS,
    _n,
)

CERTIFICATION = {
    "lookup": tested(
        "2026-11-25",
        "test_lookup.py",
        "Site evidence states checked on fixtures, including real bcftools gVCF output.",
    ),
}

NEEDS = {
    "lookup": _n(SECONDS, optional="bcftools", scale=" from existing results"),
}


HELP = "Genotype and evidence at an rsID, locus or gene, with report findings."


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "lookup", parents=[base_parser], help=HELP, description=HELP
    )
    add_arguments(parser, LookupParams)
    parser.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands.lookup", "run_lookup", LookupParams
        )
    )
