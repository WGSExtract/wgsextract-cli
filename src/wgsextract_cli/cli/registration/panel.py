from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.panel import PanelBedParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "panel", parents=[base_parser], help=CLI_HELP["cmd_panel"]
    )
    subs = parser.add_subparsers(dest="panel_cmd", required=True)
    sub = subs.add_parser(
        "bed",
        parents=[base_parser],
        help=CLI_HELP["cmd_panel-bed"],
        description=CLI_HELP["cmd_panel-bed"],
    )
    add_arguments(sub, PanelBedParams)
    sub.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands.panel", "run_panel_bed", PanelBedParams
        )
    )
