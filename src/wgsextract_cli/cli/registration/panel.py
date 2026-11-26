from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.panel import PanelAuditParams, PanelBedParams, PanelCheckParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, experimental, tested
from wgsextract_cli.commands._command_needs import (
    INSTANT,
    SECONDS,
    _n,
)
from wgsextract_cli.commands._messages.panel import CLI_HELP

_REVIEWED = "2026-11-25"
CERTIFICATION = {
    "panel bed": tested(
        _REVIEWED,
        "test_panel.py",
        "Panel coordinates, aliases and clipping checked on fixture references.",
    ),
    "panel check": tested(
        _REVIEWED,
        "test_panel_spec.py",
        "Panel inputs and interpretation checked against fixtures.",
    ),
    "panel audit": experimental(
        _REVIEWED,
        "Capability checks are not a validation of diagnostic completeness.",
        f"{REPO}/blob/main/tests/test_panel_expected_counts.py",
    ),
}

NEEDS = {
    "panel bed": _n(INSTANT),
    "panel check": _n(INSTANT),
    "panel audit": _n(SECONDS, "bcftools", optional="curl"),
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
    sub = subs.add_parser(
        "audit",
        parents=[base_parser],
        help=CLI_HELP["cmd_panel-audit"],
        description=CLI_HELP["cmd_panel-audit"],
    )
    add_arguments(sub, PanelAuditParams)
    sub.set_defaults(
        func=command_handler(
            "wgsextract_cli.commands._panel_audit", "run_panel_audit", PanelAuditParams
        )
    )
