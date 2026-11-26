from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.align import RealignParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, validated
from wgsextract_cli.commands._command_needs import (
    _SAM,
    HOURS,
    _n,
)
from wgsextract_cli.commands._messages.realign import CLI_HELP

CERTIFICATION = {
    "realign": validated(
        "2026-11-25",
        "HG002 30× SNP/indel qualification and ONT CUDA parity on tested inputs; not all backends or platforms.",
        ("Linux", "Windows"),
        f"{REPO}/issues/1121#issuecomment-7160085047",
        f"{REPO}/issues/1122#issuecomment-7040247624",
        f"{REPO}/issues/1024#issuecomment-7046241211",
        f"{REPO}/blob/main/tests/test_realign.py",
    ),
}

NEEDS = {
    "realign": _n(
        HOURS,
        *_SAM,
        optional=(
            "bcftools bgzip bwa bwa-mem2 bwa-mem3 curl gatk minimap2 pbmm2 sambamba samblaster"
        ),
    ),
}


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "realign", parents=[base_parser], help=CLI_HELP["cmd_realign"]
    )
    add_arguments(parser, RealignParams)
    parser.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands.realign", "run_realign", RealignParams
        )
    )
