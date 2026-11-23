from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.fastq import FastqArchiveParams, FastqRestoreParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    fastq = subparsers.add_parser("fastq", help=CLI_HELP["cmd_fastq"])
    commands = fastq.add_subparsers(dest="fastq_cmd", required=True)
    for name, params in (
        ("archive", FastqArchiveParams),
        ("restore", FastqRestoreParams),
    ):
        sub = commands.add_parser(
            name, parents=[base_parser], help=CLI_HELP[f"cmd_fastq-{name}"]
        )
        add_arguments(sub, params)
        sub.set_defaults(
            func=command_handler("wgsextract_cli.commands.fastq", f"cmd_{name}")
        )
