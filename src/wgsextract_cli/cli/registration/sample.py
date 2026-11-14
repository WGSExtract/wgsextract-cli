from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.sample import (
    SampleSetParams,
    SampleShowParams,
    SampleUnsetParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "sample", parents=[base_parser], help=CLI_HELP["cmd_sample"]
    )
    subs = parser.add_subparsers(dest="sample_cmd", required=True)
    for name, cls, impl in (
        ("show", SampleShowParams, "run_show"),
        ("set", SampleSetParams, "run_set"),
        ("unset", SampleUnsetParams, "run_unset"),
    ):
        sub = subs.add_parser(
            name, parents=[base_parser], help=CLI_HELP[f"cmd_sample-{name}"]
        )
        add_arguments(sub, cls)
        sub.set_defaults(
            func=command_handler("wgsextract_cli.commands.sample", impl, cls)
        )
