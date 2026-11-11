"""``wgsextract ref needs -- COMMAND...``: the reference data a command line
needs that isn't installed, and how to install it."""

import argparse
import contextlib
import io
import json
import shlex

from wgsextract_cli.core.reference_needs import missing_references
from wgsextract_cli.core.results import current_result, human_output
from wgsextract_cli.core.utils import UsageError


def parse_command(argv: list[str]) -> argparse.Namespace | None:
    """``argv`` parsed as the CLI would parse it, or None when it wouldn't
    parse (running it reports that usage error itself)."""
    from wgsextract_cli.cli.parser import build_parser, parse_args
    from wgsextract_cli.core.genome_library import apply_genome_selection

    parser, base_parser = build_parser()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            args, explicit = parse_args(parser, base_parser, argv)
    except SystemExit:
        return None
    if not hasattr(args, "func"):
        return None
    apply_genome_selection(args, set(explicit))
    return args


def cmd_ref_needs(args: argparse.Namespace) -> None:
    argv = list(args.command_line or [])
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        raise UsageError(
            "Give the command to check after --, e.g. ref needs -- info --input X.cram"
        )
    parsed = parse_command(argv)
    missing = missing_references(parsed) if parsed else []
    report = {"missing": [m.as_dict() for m in missing]}
    current_result().set_details(report)
    if args.report_json:
        human_output(json.dumps(report))
        return
    if not missing:
        human_output("All the reference data this command needs is installed.")
        return
    for item in missing:
        what = "reference genome" if item.kind == "genome" else "annotation data"
        human_output(f"Missing: {item.name} {what} (needed for {item.reason}).")
        if item.install_arguments:
            human_output(
                f"  Install it: wgsextract {shlex.join(item.install_arguments)}"
            )
