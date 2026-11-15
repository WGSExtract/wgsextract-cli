from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.repair import RepairFtdnaBamParams, RepairFtdnaVcfParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP

repair_bam = command_handler("wgsextract_cli.commands.repair", "repair_bam")
repair_vcf = command_handler("wgsextract_cli.commands.repair", "repair_vcf")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "repair", help="Repair formatting violations in FTDNA files."
    )
    repair_subs = parser.add_subparsers(dest="repair_cmd", required=True)
    bam_parser = repair_subs.add_parser(
        "ftdna-bam", parents=[base_parser], help=CLI_HELP["cmd_repair-ftdna-bam"]
    )
    add_arguments(bam_parser, RepairFtdnaBamParams)
    bam_parser.set_defaults(func=repair_bam)
    vcf_parser = repair_subs.add_parser(
        "ftdna-vcf", parents=[base_parser], help=CLI_HELP["cmd_repair-ftdna-vcf"]
    )
    add_arguments(vcf_parser, RepairFtdnaVcfParams)
    vcf_parser.set_defaults(func=repair_vcf)
