from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.microarray import MicroarrayParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, validated
from wgsextract_cli.commands._command_needs import (
    MINUTES,
    _n,
)
from wgsextract_cli.commands._messages.microarray import CLI_HELP

CERTIFICATION = {
    "microarray": validated(
        "2026-11-25",
        "HG00096 GRCh38 and CHM13 vendor exports compared with independent chip truth in sampled windows, not at every chip site.",
        ("Linux",),
        f"{REPO}/blob/main/docs/microarray-benchmarks.md",
        f"{REPO}/blob/main/docs/install_testing.md#what-one-run-covers",
        f"{REPO}/blob/main/tests/test_microarray_chm13_integration.py",
    ),
}

NEEDS = {
    "microarray": _n(
        MINUTES, "bcftools", "bgzip", "samtools", "tabix", optional="curl"
    ),
}


run = command_handler("wgsextract_cli.commands.microarray", "run")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "microarray",
        parents=[base_parser],
        help=CLI_HELP["cmd_microarray"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    add_arguments(parser, MicroarrayParams)
    parser.set_defaults(func=run)
