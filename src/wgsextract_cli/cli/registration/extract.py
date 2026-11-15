from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.extract import (
    BamSubsetParams,
    CustomParams,
    ExtractRegionParams,
    MitoFastaParams,
    MitoVcfParams,
    MtBamParams,
    UnmappedParams,
    YdnaBamParams,
    YdnaVcfParams,
    YMtExtractParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP

cmd_bam_subset = command_handler("wgsextract_cli.commands.extract", "cmd_bam_subset")
cmd_custom = command_handler("wgsextract_cli.commands.extract", "cmd_custom")
cmd_mito_fasta = command_handler("wgsextract_cli.commands.extract", "cmd_mito_fasta")
cmd_mito_vcf = command_handler("wgsextract_cli.commands.extract", "cmd_mito_vcf")
cmd_mt_bam = command_handler("wgsextract_cli.commands.extract", "cmd_mt_bam")
cmd_region = command_handler("wgsextract_cli.commands.extract", "cmd_region")
cmd_unmapped = command_handler("wgsextract_cli.commands.extract", "cmd_unmapped")
cmd_y_mt_extract = command_handler(
    "wgsextract_cli.commands.extract", "cmd_y_mt_extract"
)
cmd_ydna_bam = command_handler("wgsextract_cli.commands.extract", "cmd_ydna_bam")
cmd_ydna_vcf = command_handler("wgsextract_cli.commands.extract", "cmd_ydna_vcf")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "extract", help="Extract specific chromosomes or unmapped reads."
    )
    ext_subs = parser.add_subparsers(dest="ext_cmd", required=True)
    for name, cls, func in (
        ("mito-fasta", MitoFastaParams, cmd_mito_fasta),
        ("mito-vcf", MitoVcfParams, cmd_mito_vcf),
        ("mt-bam", MtBamParams, cmd_mt_bam),
        ("bam-subset", BamSubsetParams, cmd_bam_subset),
        ("ydna-bam", YdnaBamParams, cmd_ydna_bam),
        ("ydna-vcf", YdnaVcfParams, cmd_ydna_vcf),
        ("y-mt-extract", YMtExtractParams, cmd_y_mt_extract),
        ("unmapped", UnmappedParams, cmd_unmapped),
        ("custom", CustomParams, cmd_custom),
        ("region", ExtractRegionParams, cmd_region),
    ):
        sub = ext_subs.add_parser(
            name, parents=[base_parser], help=CLI_HELP[f"cmd_{name}"]
        )
        add_arguments(sub, cls)
        sub.set_defaults(func=func)
