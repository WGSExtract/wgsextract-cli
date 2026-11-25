#!/usr/bin/env python3

import logging
import os
import sys
from collections.abc import Sequence
from contextlib import ExitStack

from .cli.bootstrap import configure_stdio_encoding
from .cli.help import print_full_help, run_help
from .cli.parser import build_parser, config_path_from_argv, parse_args
from .core.config import reload_settings, set_config_path
from .core.errors import WGSExtractError
from .core.workspace_activity import WORKSPACE_LEASE_ARGUMENT, workspace_use

__all__ = ["main"]


def main(argv: Sequence[str] | None = None, *, admitted_by: str | None = None) -> None:
    configure_stdio_encoding()
    with ExitStack() as workspace:
        try:
            workspace.enter_context(workspace_use(admitted_by))
        except (OSError, WGSExtractError) as exc:
            print(f"Cannot use the workspace: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        _run(argv, workspace)


def _run(argv: Sequence[str] | None, workspace: ExitStack) -> None:
    try:
        set_config_path(config_path_from_argv(sys.argv[1:] if argv is None else argv))
        reload_settings()
    except WGSExtractError as exc:
        detail = " ".join(str(exc).splitlines())
        print(
            f"Configuration error: {detail} Edit the file directly or use "
            "--config PATH; config set/show are not supported.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    parser, base_parser = build_parser(lazy=True)
    args, explicit_dests = parse_args(parser, base_parser, argv)
    args._argv = list(sys.argv[1:] if argv is None else argv)

    if args.full_help:
        print_full_help(parser)
        raise SystemExit(0)
    if args.command == "help":
        # Read-only and quiet: no run log, and --json prints the command
        # index rather than a result manifest.
        run_help(args, parser)
        return

    from .cli.bootstrap import (
        configure_logging,
        install_signal_handlers,
        start_parent_monitor,
    )
    from .core.run_log import RunLogError, run_logging

    configure_logging(debug=args.debug, quiet=args.quiet)
    install_signal_handlers()
    start_parent_monitor(args.parent_pid)

    # The desktop starts its server before submitting a job and keeps it idle
    # between jobs. The app guards submissions; each worker owns its own lease.
    if (
        args.command == "serve"
        and args.parent_pid
        and os.getenv("WGSEXTRACT_GUI_APP_DATA")
    ):
        workspace.close()

    try:
        with run_logging(args, sys.argv[1:] if argv is None else argv):
            if hasattr(args, "func"):
                from .cli.dispatch import dispatch_command

                dispatch_command(args, explicit_dests)
            else:
                parser.print_help()
    except RunLogError as exc:
        logging.error("Cannot write persistent run log: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0].startswith(WORKSPACE_LEASE_ARGUMENT):
        main(argv[1:], admitted_by=argv[0][len(WORKSPACE_LEASE_ARGUMENT) :])
    else:
        main(argv)
