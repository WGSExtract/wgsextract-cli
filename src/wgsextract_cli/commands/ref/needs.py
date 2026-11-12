"""``wgsextract ref needs -- COMMAND...``: the reference data a command line
needs that isn't installed, and how to install it."""

import argparse
import contextlib
import io
import json
import shlex
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from wgsextract_cli.core import sample_workspace
from wgsextract_cli.core.reference_needs import MissingReference, missing_references
from wgsextract_cli.core.results import current_result, human_output
from wgsextract_cli.core.utils import UsageError, WGSExtractError


@contextlib.contextmanager
def command_config(config: str | None) -> Iterator[bool]:
    """The checked command's own ``--config`` loaded for the check, and the
    outer settings restored afterwards. Yields False when that config can't
    be loaded (running the command reports it)."""
    if not config:
        yield True
        return
    from wgsextract_cli.core import config as cfg

    outer = cfg._config_path_override
    try:
        cfg.set_config_path(config)
        try:
            cfg.reload_settings()
        except WGSExtractError:
            yield False
            return
        yield True
    finally:
        cfg.set_config_path(outer)
        cfg.reload_settings()


def _with_config(item: MissingReference, config: str | None) -> MissingReference:
    """``item`` installing into the checked command's config's library."""
    if not config or not item.install_arguments:
        return item
    path = str(Path(config).expanduser().resolve())
    return replace(item, install_arguments=["--config", path, *item.install_arguments])


def check_command(argv: list[str]) -> list[MissingReference]:
    """What the command line ``argv`` needs and lacks, read under its own
    ``--config`` and without recording anything in the sample workspace."""
    from wgsextract_cli.cli.parser import config_path_from_argv

    config = config_path_from_argv(argv)
    with command_config(config) as loaded:
        if not loaded:
            return []
        parsed = parse_command(argv)
        if not parsed:
            return []
        with sample_workspace.recording_paused():
            missing = missing_references(parsed)
    return [_with_config(item, config) for item in missing]


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
    missing = check_command(argv)
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
