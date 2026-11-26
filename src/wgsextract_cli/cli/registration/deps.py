from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api._params import Params
from wgsextract_cli.api.deps import (
    DepsCheckParams,
    DepsInstallParams,
    DepsListParams,
    DepsReadyParams,
    DepsUninstallParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, experimental, tested
from wgsextract_cli.commands._command_needs import (
    DOWNLOAD,
    SECONDS,
    _n,
)
from wgsextract_cli.commands._messages.deps import CLI_HELP

_REVIEWED = "2026-11-25"
CERTIFICATION = {
    "deps check": tested(
        _REVIEWED, "test_dependency_probes.py", "Local dependency probes checked."
    ),
    "deps list": tested(
        _REVIEWED, "test_deps_manage.py", "Local dependency inventory checked."
    ),
    "deps ready": tested(
        _REVIEWED, "test_deps_ready.py", "Local dependency readiness checked."
    ),
    "deps install": experimental(
        _REVIEWED,
        "External installers are mocked in fixture tests.",
        f"{REPO}/blob/main/tests/test_deps_manage.py",
    ),
    "deps uninstall": experimental(
        _REVIEWED,
        "External uninstallers are mocked in fixture tests.",
        f"{REPO}/blob/main/tests/test_deps_manage.py",
    ),
}

NEEDS = {
    "deps check": _n(SECONDS, scale=""),
    "deps list": _n(SECONDS, scale=""),
    "deps ready": _n(SECONDS, scale=""),
    "deps install": _n(DOWNLOAD, optional="java java17"),
    "deps uninstall": _n(SECONDS, scale=""),
}


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    deps_parser = subparsers.add_parser("deps", help=CLI_HELP["cmd_deps"])
    deps_subparsers = deps_parser.add_subparsers(dest="subcommand", required=True)

    def add(name: str, help_key: str, params: type[Params]) -> argparse.ArgumentParser:
        parser = deps_subparsers.add_parser(
            name, parents=[base_parser], help=CLI_HELP[help_key]
        )
        add_arguments(parser, params)
        return parser

    # Literal module and function names: CI's test selection reads them.
    add("check", "cmd_check-deps", DepsCheckParams).set_defaults(
        func=command_handler(
            "wgsextract_cli.commands._deps_status",
            "run",
            DepsCheckParams,
            debug="debug",
        )
    )
    add("list", "cmd_deps-list", DepsListParams).set_defaults(
        func=command_handler(
            "wgsextract_cli.commands._deps_status", "run_list", DepsListParams
        )
    )
    add("ready", "cmd_deps-ready", DepsReadyParams).set_defaults(
        func=command_handler(
            "wgsextract_cli.commands._deps_status", "run_ready", DepsReadyParams
        )
    )
    add("install", "cmd_deps-install", DepsInstallParams).set_defaults(
        func=command_handler(
            "wgsextract_cli.commands._deps_status", "run_install", DepsInstallParams
        )
    )
    add("uninstall", "cmd_deps-uninstall", DepsUninstallParams).set_defaults(
        func=command_handler(
            "wgsextract_cli.commands._deps_status",
            "run_uninstall",
            DepsUninstallParams,
        )
    )
