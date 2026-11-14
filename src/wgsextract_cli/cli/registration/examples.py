from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.examples import (
    ExampleGenomeDownloadParams,
    ExampleGenomeListParams,
)
from wgsextract_cli.cli._lazy import command_handler


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    """Register the example-genome list/download subcommands."""
    parser = subparsers.add_parser(
        "example-genome", help="Download curated 1000 Genomes example datasets."
    )
    examples_subs = parser.add_subparsers(dest="examples_cmd", required=True)
    for name, cls, impl, help_text in (
        (
            "list",
            ExampleGenomeListParams,
            "run_list",
            "List curated 1000 Genomes examples.",
        ),
        (
            "download",
            ExampleGenomeDownloadParams,
            "run_download",
            "Download curated 1000 Genomes examples into the dataset library.",
        ),
    ):
        sub = examples_subs.add_parser(name, parents=[base_parser], help=help_text)
        add_arguments(sub, cls)
        sub.set_defaults(
            func=command_handler("wgsextract_cli.commands.examples", impl, cls)
        )
