from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.info import (
    CalculateCoverageParams,
    ChipParams,
    CoverageSampleParams,
    InfoGeneParams,
    InfoParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._command_needs import (
    _SAM,
    MINUTES,
    SECONDS,
    _n,
)
from wgsextract_cli.core.messages import CLI_HELP

NEEDS = {
    "info": _n(SECONDS, *_SAM, optional="bcftools"),
    "info calculate-coverage": _n(MINUTES, *_SAM, optional="bcftools"),
    "info coverage-sample": _n(MINUTES, *_SAM, optional="bcftools"),
    "info gene": _n(SECONDS, *_SAM, optional="bcftools tabix", scale=" for a gene"),
    "info chip": _n(SECONDS, scale=" for a raw-data file"),
}


run = command_handler("wgsextract_cli.commands.info.runner", "run")
chip_handler = command_handler("wgsextract_cli.commands.info.chip", "run")
gene_handler = command_handler("wgsextract_cli.commands.info.gene", "run")


def _inherit_format(parser: argparse.ArgumentParser) -> None:
    """Without its own --format, a coverage subcommand keeps the one given
    before it (`info --format parquet calculate-coverage`)."""
    for action in parser._actions:
        if action.dest == "table_format":
            action.default = argparse.SUPPRESS


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "info", parents=[base_parser], help=CLI_HELP["cmd_info"]
    )
    add_arguments(parser, InfoParams)
    parser.add_argument("--csv", action="store_true", help=CLI_HELP["arg_csv"])
    info_subs = parser.add_subparsers(dest="info_cmd", required=False)
    calc_cov = info_subs.add_parser(
        "calculate-coverage",
        parents=[base_parser],
        help=CLI_HELP["cmd_calculate-coverage"],
    )
    add_arguments(calc_cov, CalculateCoverageParams)
    _inherit_format(calc_cov)
    calc_cov.set_defaults(func=run)
    samp_cov = info_subs.add_parser(
        "coverage-sample", parents=[base_parser], help=CLI_HELP["cmd_coverage-sample"]
    )
    add_arguments(samp_cov, CoverageSampleParams)
    _inherit_format(samp_cov)
    samp_cov.set_defaults(func=run)
    gene_parser = info_subs.add_parser(
        "gene",
        parents=[base_parser],
        help=CLI_HELP["cmd_info-gene"],
        description=CLI_HELP["cmd_info-gene"],
    )
    add_arguments(gene_parser, InfoGeneParams)
    gene_parser.set_defaults(func=gene_handler)
    chip_parser = info_subs.add_parser(
        "chip", parents=[base_parser], help=CLI_HELP["cmd_info-chip"]
    )
    add_arguments(chip_parser, ChipParams)
    chip_parser.set_defaults(func=chip_handler)
    parser.set_defaults(func=run)
