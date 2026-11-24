from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.panel import PanelBedParams, PanelCheckParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._command_needs import (
    INSTANT,
    _n,
)
from wgsextract_cli.core.messages import CLI_HELP

NEEDS = {
    "panel bed": _n(INSTANT),
    "panel check": _n(INSTANT),
}


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
    sub = subs.add_parser(
        "check",
        parents=[base_parser],
        help=CLI_HELP["cmd_panel-check"],
        description=CLI_HELP["cmd_panel-check"],
    )
    add_arguments(sub, PanelCheckParams)
    sub.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands._panel_check", "run_panel_check", PanelCheckParams
        )
    )
