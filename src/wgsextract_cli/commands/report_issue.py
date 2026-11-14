"""``wgsextract report-issue``: preview a redacted bug report and open it as a
pre-filled GitHub issue."""

from __future__ import annotations

import argparse
import dataclasses
import sys
import textwrap

from wgsextract_cli.api import support
from wgsextract_cli.api._params import from_namespace
from wgsextract_cli.cli.registration.report_issue import register as register
from wgsextract_cli.core.issue_report import IssueReport
from wgsextract_cli.core.results import current_result, human_output, json_mode


def run(args: argparse.Namespace) -> None:
    params = from_namespace(support.ReportIssueParams, args)
    if "outdir" not in getattr(args, "_explicit_dests", set()):
        # Not the configured output_directory: reports go to the data folder.
        params = dataclasses.replace(params, outdir=None)
    report = support.build_issue_report(params)
    path = support.save_issue_report(report, params.outdir)
    result = current_result()
    result.add_output(path, "issue_report", kind="markdown")

    _preview(report, path)
    opened = False
    if args.print_only:
        pass
    elif args.open_browser or _confirmed():
        opened = support.open_issue(report)
        if not opened:
            human_output("Could not start a browser; open the link above yourself.")
    elif not json_mode():
        human_output(
            "Review the report above, then open the link to submit it, or run "
            "again with --open."
        )
    result.set_details({**report.to_dict(), "file": str(path), "opened": opened})


def _preview(report: IssueReport, path: object) -> None:
    rule = "-" * 72
    removed = ", ".join(f"{n} {kind}" for kind, n in report.redactions.items())
    human_output(f"Bug report about: {report.source}")
    human_output(f"Removed for privacy: {removed or 'nothing found'}")
    for note in report.notes:
        human_output(f"Note: {note}")
    human_output(rule)
    human_output(f"Title: {report.title}\n")
    human_output(report.link_diagnostics.rstrip())
    human_output(rule)
    if report.truncated:
        cuts = "; ".join(report.truncated)
        human_output(
            textwrap.fill(
                f"Shortened to fit in the link ({cuts}). The full report is "
                f"saved at {path}; paste from it into the issue if useful.",
                width=78,
            )
        )
    else:
        human_output(f"Saved at {path}")
    human_output(f"\nLink ({len(report.url)} characters):\n{report.url}\n")


def _confirmed() -> bool:
    """Ask on an interactive terminal; never under --json or in a pipe."""
    if json_mode() or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        answer = input("Open this report on GitHub in your browser? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")
