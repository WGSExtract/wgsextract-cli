from __future__ import annotations

import argparse
from typing import Any

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api._params import Params
from wgsextract_cli.api.qc import (
    FakeDataParams,
    FastpParams,
    FastqcParams,
    MultiqcMetricsParams,
    MultiqcParams,
    QcAncestryParams,
    QcContaminationParams,
    QcVcfParams,
    RelateParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.core.messages import CLI_HELP

cmd_ancestry = command_handler("wgsextract_cli.commands.qc.ancestry", "cmd_ancestry")
cmd_contamination = command_handler(
    "wgsextract_cli.commands.qc.contamination", "cmd_contamination"
)
cmd_fake_data = command_handler("wgsextract_cli.commands.qc", "cmd_fake_data")
cmd_fastp = command_handler("wgsextract_cli.commands.qc.commands", "cmd_fastp")
cmd_fastqc = command_handler("wgsextract_cli.commands.qc.commands", "cmd_fastqc")
cmd_multiqc = command_handler("wgsextract_cli.commands.qc.commands", "cmd_multiqc")
cmd_multiqc_metrics = command_handler(
    "wgsextract_cli.commands.qc.commands", "cmd_multiqc_metrics"
)
cmd_relate = command_handler("wgsextract_cli.commands.qc", "cmd_relate")
cmd_vcf_qc = command_handler("wgsextract_cli.commands.qc.commands", "cmd_vcf_qc")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser("qc", help=CLI_HELP["cmd_qc"])
    qc_subs = parser.add_subparsers(dest="qc_cmd", required=True)
    commands: tuple[tuple[str, type[Params], Any, str], ...] = (
        ("fastp", FastpParams, cmd_fastp, CLI_HELP["cmd_fastp"]),
        ("fastqc", FastqcParams, cmd_fastqc, CLI_HELP["cmd_fastqc"]),
        (
            "multiqc",
            MultiqcParams,
            cmd_multiqc,
            "Combine existing FastQC reports, bcftools stats and WGS Extract result manifests into one local HTML report.",
        ),
        (
            "multiqc-metrics",
            MultiqcMetricsParams,
            cmd_multiqc_metrics,
            "Write MultiQC custom-content files (wgsextract_*_mqc.json) from result manifests; MultiQC isn't needed.",
        ),
        ("vcf", QcVcfParams, cmd_vcf_qc, CLI_HELP["cmd_vcf-qc"]),
        ("relate", RelateParams, cmd_relate, CLI_HELP["cmd_qc-relate"]),
        ("ancestry", QcAncestryParams, cmd_ancestry, CLI_HELP["cmd_qc-ancestry"]),
        (
            "contamination",
            QcContaminationParams,
            cmd_contamination,
            CLI_HELP["cmd_qc-contamination"],
        ),
        ("fake-data", FakeDataParams, cmd_fake_data, CLI_HELP["cmd_fake-data"]),
    )
    for name, cls, func, help_text in commands:
        sub = qc_subs.add_parser(name, parents=[base_parser], help=help_text)
        add_arguments(sub, cls)
        sub.set_defaults(func=func)
        if cls is QcVcfParams:
            sub.set_defaults(_one_vcf_input=True)  # see dispatch
