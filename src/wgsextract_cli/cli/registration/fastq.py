from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.fastq import FastqArchiveParams, FastqRestoreParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import tested
from wgsextract_cli.commands._command_needs import MINUTES, _n

_REVIEWED = "2026-11-25"
CERTIFICATION = {
    "fastq archive": tested(
        _REVIEWED,
        "test_fastq_archive.py",
        "Byte-preserving FASTQ archive round trips on fixtures.",
    ),
    "fastq restore": tested(
        _REVIEWED,
        "test_fastq_archive.py",
        "Archive integrity and byte-exact FASTQ restoration on fixtures.",
    ),
}

NEEDS = {
    "fastq archive": _n(MINUTES),
    "fastq restore": _n(MINUTES),
}


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    fastq = subparsers.add_parser(
        "fastq", help="Archive and restore FASTQ reads without a reference."
    )
    commands = fastq.add_subparsers(dest="fastq_cmd", required=True)
    for name, params, help_text in (
        (
            "archive",
            FastqArchiveParams,
            "Compress one or two FASTQ files into a byte-exact archive.",
        ),
        (
            "restore",
            FastqRestoreParams,
            "Restore and verify plain FASTQ bytes from an archive.",
        ),
    ):
        sub = commands.add_parser(name, parents=[base_parser], help=help_text)
        add_arguments(sub, params)
        sub.set_defaults(
            func=command_handler("wgsextract_cli.commands.fastq", f"cmd_{name}")
        )
