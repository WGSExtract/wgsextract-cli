from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.analyze import AnalyzeBatchGenParams, AnalyzeComprehensiveParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser("analyze", help=CLI_HELP["cmd_analyze"])
    analyze_subs = parser.add_subparsers(dest="analyze_cmd", required=True)
    comp_parser = analyze_subs.add_parser(
        "comprehensive", parents=[base_parser], help=CLI_HELP["cmd_comprehensive"]
    )
    add_arguments(comp_parser, AnalyzeComprehensiveParams)
    comp_parser.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands.analyze",
            "run_comprehensive",
            AnalyzeComprehensiveParams,
            debug="debug",
            keep_awake="keep_awake",
        )
    )
    gen_batch_parser = analyze_subs.add_parser(
        "batch-gen", parents=[base_parser], help=CLI_HELP["cmd_batch_gen"]
    )
    add_arguments(gen_batch_parser, AnalyzeBatchGenParams)
    gen_batch_parser.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands.analyze", "run_batch_gen", AnalyzeBatchGenParams
        )
    )
