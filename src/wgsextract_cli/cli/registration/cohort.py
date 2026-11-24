from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.cohort import CohortSummarizeParams
from wgsextract_cli.cli._lazy import command_handler


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser("cohort", help="Summarize a local variant cohort.")
    subs = parser.add_subparsers(dest="cohort_cmd", required=True)
    summary = subs.add_parser(
        "summarize", parents=[base_parser], help=CohortSummarizeParams.__doc__
    )
    add_arguments(summary, CohortSummarizeParams)
    summary.set_defaults(
        _private_provenance=CohortSummarizeParams.PRIVATE_PROVENANCE,
        func=command_handler(
            "wgsextract_cli.commands.cohort", "run_summary", CohortSummarizeParams
        ),
    )
