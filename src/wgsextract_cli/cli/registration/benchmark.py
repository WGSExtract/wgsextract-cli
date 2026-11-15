from __future__ import annotations

import argparse
from functools import wraps

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.benchmark import BenchmarkParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "benchmark",
        parents=[base_parser],
        help=CLI_HELP["cmd_benchmark"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    add_arguments(parser, BenchmarkParams)
    handler = command_handler(
        "wgsextract_cli.commands.benchmark.runner",
        "run_benchmark",
        BenchmarkParams,
        debug="debug",
        quiet="quiet",
        keep_awake="keep_awake",
    )

    @wraps(handler)
    def run(args: argparse.Namespace) -> None:
        # Benchmarks use out/benchmark, not config output_directory.
        if "outdir" not in getattr(args, "_explicit_dests", set()):
            args.outdir = None
        handler(args)

    parser.set_defaults(func=run)
