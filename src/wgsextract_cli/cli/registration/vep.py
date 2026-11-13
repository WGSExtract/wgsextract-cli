from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.vep import (
    VepDeleteParams,
    VepDownloadParams,
    VepRunParams,
    VepVerifyParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP

cli_vep_delete = command_handler("wgsextract_cli.commands.vep", "cli_vep_delete")
cli_vep_download = command_handler("wgsextract_cli.commands.vep", "cli_vep_download")
cli_vep_run = command_handler("wgsextract_cli.commands.vep", "cli_vep_run")
cli_vep_verify = command_handler("wgsextract_cli.commands.vep", "cli_vep_verify")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser("vep", help=CLI_HELP["cmd_vep"])
    vep_subs = parser.add_subparsers(dest="vep_cmd", required=True)
    dl_parser = vep_subs.add_parser(
        "download", parents=[base_parser], help=CLI_HELP["cmd_vep-download"]
    )
    add_arguments(dl_parser, VepDownloadParams, config_defaults=True)
    dl_parser.set_defaults(func=cli_vep_download)
    verify_parser = vep_subs.add_parser(
        "verify", parents=[base_parser], help=CLI_HELP["cmd_vep-verify"]
    )
    add_arguments(verify_parser, VepVerifyParams, config_defaults=True)
    verify_parser.set_defaults(func=cli_vep_verify)
    delete_parser = vep_subs.add_parser(
        "delete", parents=[base_parser], help=CLI_HELP["cmd_vep-delete"]
    )
    add_arguments(delete_parser, VepDeleteParams, config_defaults=True)
    delete_parser.set_defaults(func=cli_vep_delete)
    run_parser = vep_subs.add_parser(
        "run", parents=[base_parser], help=CLI_HELP["cmd_vep-run"]
    )
    add_arguments(run_parser, VepRunParams, config_defaults=True)
    run_parser.set_defaults(func=cli_vep_run)
