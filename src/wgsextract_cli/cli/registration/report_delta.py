from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.report import ReportDeltaParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import tested
from wgsextract_cli.commands._command_needs import (
    INSTANT,
    _n,
)

CERTIFICATION = {
    "report-delta": tested(
        "2026-11-25",
        "test_release_recheck_delta.py",
        "Differences checked on paired fixture reports.",
    ),
}

NEEDS = {
    "report-delta": _n(INSTANT),
}


HELP = (
    "Compare two runs' result manifests: findings added, removed or "
    "reclassified, changed releases, failed sections."
)


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "report-delta",
        parents=[base_parser],
        help=HELP,
        description=HELP
        + " Timestamps, ordering and file paths are ignored; reads only the two"
        " manifests and makes no network request. See docs/recheck-and-delta.md.",
    )
    add_arguments(parser, ReportDeltaParams)
    parser.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands.report_delta", "run_delta", ReportDeltaParams
        )
    )
