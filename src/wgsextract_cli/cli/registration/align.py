from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.align import AlignParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, validated
from wgsextract_cli.commands._command_needs import (
    _SAM,
    HOURS,
    _n,
)
from wgsextract_cli.commands._messages.align import CLI_HELP

CERTIFICATION = {
    "align": validated(
        "2026-11-25",
        "CPU/CUDA decoded-record parity on 5 million HG002 Illumina read pairs; not an all-backend qualification.",
        ("Windows",),
        f"{REPO}/blob/main/docs/windows.md#wgse-bwa-and-optional-nvidia-cuda",
        f"{REPO}/blob/main/tests/test_alignment_commands.py",
    ),
}

NEEDS = {
    "align": _n(
        HOURS,
        *_SAM,
        optional="bcftools bwa bwa-mem2 bwa-mem3 curl gatk minimap2 pbmm2 sambamba samblaster",
    ),
}


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "align", parents=[base_parser], help=CLI_HELP["cmd_align"]
    )
    add_arguments(parser, AlignParams)
    parser.set_defaults(
        func=command_handler("wgsextract_cli.commands.align", "run_align", AlignParams)
    )
