"""``wgsextract ref needs -- COMMAND...``: the reference data and optional
tools a command line needs that aren't installed, and how to install them."""

import argparse
import contextlib
import io
import json
import shlex
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from wgsextract_cli.core import sample_workspace
from wgsextract_cli.core.optional_tools import installable, missing_tools
from wgsextract_cli.core.ref_library.needs import MissingReference, missing_references
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
    from wgsextract_cli.core.config import scoped_config

    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(scoped_config(config))
        except WGSExtractError:
            yield False
            return
        yield True


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
        missing += [_missing_tool(tool, parsed) for tool in missing_tools(parsed)]
    return [_with_config(item, config) for item in missing]


def _missing_tool(tool: str, args: argparse.Namespace) -> MissingReference:
    """A missing optional tool, installed by ``deps install`` when it can be;
    otherwise the user installs it."""
    return MissingReference(
        id=tool,
        kind="tool",
        name=tool,
        reason=str(getattr(args, "_command", "")),
        install_arguments=["deps", "install", tool] if installable(tool) else None,
    )


def parse_command(argv: list[str]) -> argparse.Namespace | None:
    """``argv`` parsed as the CLI would parse it, or None when it wouldn't
    parse (running it reports that usage error itself)."""
    try:
        return parse_command_line(argv)
    except UsageError:
        return None


def parse_command_line(
    argv: list[str], *, persist_genome: bool = True
) -> argparse.Namespace:
    """``argv`` parsed as the CLI would parse it; a usage error with
    argparse's own message when it wouldn't parse or names no command.
    Without ``persist_genome``, resolving ``--genome`` saves nothing in the
    genome's folder."""
    from wgsextract_cli.cli.parser import build_parser, parse_args
    from wgsextract_cli.core.genome_library import apply_genome_selection

    parser, base_parser = build_parser()
    errors = io.StringIO()
    try:
        with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(errors):
            args, explicit = parse_args(parser, base_parser, argv)
    except SystemExit as error:
        lines = errors.getvalue().strip().splitlines()
        raise UsageError(
            lines[-1] if lines else f"{shlex.join(argv)} doesn't parse"
        ) from error
    if not hasattr(args, "func"):
        raise UsageError(f"{shlex.join(argv)} names no command to run.")
    apply_genome_selection(args, set(explicit), persist=persist_genome)
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
        human_output(
            "All the reference data and tools this command needs are installed."
        )
        return
    kinds = {"genome": "reference genome", "tool": "optional tool"}
    for item in missing:
        what = kinds.get(item.kind, "annotation data")
        human_output(f"Missing: {item.name} {what} (needed for {item.reason}).")
        if item.install_arguments:
            human_output(
                f"  Install it: wgsextract {shlex.join(item.install_arguments)}"
            )
