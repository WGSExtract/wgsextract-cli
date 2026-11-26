from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.examples import (
    ExampleGenomeDownloadParams,
    ExampleGenomeListParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, experimental, tested
from wgsextract_cli.commands._command_needs import (
    DOWNLOAD,
    INSTANT,
    _n,
)

_REVIEWED = "2026-11-25"
CERTIFICATION = {
    "example-genome list": tested(
        _REVIEWED,
        "test_logic.py",
        "Local example catalog entries and tag selection checked.",
    ),
    "example-genome download": experimental(
        _REVIEWED,
        "Resume and publication are exercised against a local HTTP server, but public sources and all transports are not qualified.",
        f"{REPO}/blob/main/tests/test_download_file_http.py",
    ),
}

NEEDS = {
    "example-genome list": _n(INSTANT),
    "example-genome download": _n(DOWNLOAD, optional="curl"),
}


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
