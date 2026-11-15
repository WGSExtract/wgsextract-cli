from __future__ import annotations

import argparse

from wgsextract_cli.api import support
from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.cli._lazy import command_handler

run = command_handler("wgsextract_cli.commands.report_issue", "run")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "report-issue",
        parents=[base_parser],
        help="Report a problem: preview a redacted bug report, then open it as a pre-filled GitHub issue.",
        description="Build a bug report about this installation and the last run (or --run FILE), with names, paths, reads, genotypes and secrets removed. It is shown first and saved locally; nothing is uploaded. The browser opens GitHub's new-issue form only after you agree (or with --open), and you submit it yourself.",
    )
    add_arguments(parser, support.ReportIssueParams)
    browser = parser.add_mutually_exclusive_group()
    browser.add_argument(
        "--print",
        "--dry-run",
        dest="print_only",
        action="store_true",
        help="Only print the report and its link; never open the browser.",
    )
    browser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the pre-filled issue in the browser without asking.",
    )
    parser.set_defaults(func=run)
