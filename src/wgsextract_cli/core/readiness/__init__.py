"""``wgsextract deps ready -- COMMAND ...``: whether a command line can run
on this computer offline, before a long run or before disconnecting.

The command line is parsed as the CLI parses it, under its own ``--config``,
and each prerequisite is checked by the resolver the command itself uses:
the platform refusals (``verify_platform_support``), the tools it runs
(``COMMAND_NEEDS`` and the options that select tools, found and probed as
``deps check`` does), its input, reference genome and annotation data as
``ref needs`` resolves them, and for ``vcf clair3`` and ``vcf deepvariant``
the backend, images, GPUs and model weights their runs verify. Nothing is
installed, pulled, downloaded or written, and no network request is made.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from wgsextract_cli.core.utils import UsageError

from .model import UNSUPPORTED, UNVERIFIED, Item, Report

# Commands whose run-time data deps ready checks in full: the input, its
# reference genome and, for annotation, the annotation file. Others also look
# up library data (caches, panels, catalogs, N-count tables, trees) when they
# run, which isn't checked yet: their readiness stays unverified.
DATA_CHECKED = frozenset(
    {
        "bam identify", "bam index", "bam merge", "bam sort", "bam to-bam",
        "bam to-cram", "bam to-ubam", "bam unalign", "bam unindex", "bam unsort",
        "extract bam-subset", "extract custom", "extract mt-bam",
        "extract unmapped", "extract y-mt-extract", "extract ydna-bam",
        "qc fastp", "qc fastqc", "qc vcf",
        "vcf alphamissense", "vcf cadd", "vcf call", "vcf clair3", "vcf clinvar",
        "vcf deepvariant", "vcf filter", "vcf freebayes", "vcf gerp",
        "vcf gnomad", "vcf indel", "vcf pharmgkb", "vcf phylop", "vcf revel",
        "vcf snp", "vcf spliceai", "vcf table",
    }
)  # fmt: skip

# Tools a caller family reports itself, as its backend.
_FAMILY_TOOLS = {
    "vcf clair3": {"docker", "run_clair3.sh", "wgse-clair3"},
    "vcf deepvariant": {"docker", "run_deepvariant", "wgse-deepvariant"},
}


def inspect_command(argv: list[str]) -> Report:
    """The readiness of the command line ``argv``."""
    from wgsextract_cli.cli.parser import config_path_from_argv
    from wgsextract_cli.commands.ref.needs import command_config, parse_command_line
    from wgsextract_cli.core import sample_workspace
    from wgsextract_cli.core.results import redact_argv

    if not argv:
        raise UsageError(
            "Give the command to check after --, e.g. "
            "deps ready -- vcf snp --input sample.bam"
        )
    config = config_path_from_argv(argv)
    with command_config(config) as loaded:
        if not loaded:
            raise UsageError("The command line's --config can't be loaded.")
        args = parse_command_line(argv, persist_genome=False)
        command = str(getattr(args, "_command", "") or "")
        report = Report(
            command,
            redact_argv(argv),
            {
                "os": sys.platform,
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
        )
        with sample_workspace.recording_paused():
            _check(report, command, args)
    if config:
        # Fixes act on the library and settings of the config checked.
        path = str(Path(config).expanduser().resolve())
        for item in report.items:
            if item.fix_arguments:
                item.fix_arguments = ["--config", path, *item.fix_arguments]
    return report


def _check(report: Report, command: str, args: object) -> None:
    import argparse

    from wgsextract_cli.cli.platform_support import verify_platform_support
    from wgsextract_cli.commands._command_needs import COMMAND_NEEDS, DOWNLOAD
    from wgsextract_cli.core.optional_tools import command_tools, executable_override
    from wgsextract_cli.core.utils import WGSExtractError

    from .components import tool_item
    from .data import data_items

    assert isinstance(args, argparse.Namespace)
    needs = COMMAND_NEEDS.get(command)
    if needs is not None and needs.runtime == DOWNLOAD:
        report.add(
            Item(
                "network",
                command,
                UNSUPPORTED,
                detail="this command downloads; run it while online",
            )
        )
    try:
        verify_platform_support(args)
    except WGSExtractError as error:
        report.add(Item("platform", command, UNSUPPORTED, detail=str(error)))
    family = _FAMILY_TOOLS.get(command, set())
    for tool in command_tools(args):
        if tool not in family:
            report.add(tool_item(tool, override=executable_override(tool, args)))
    data = data_items(command, args)
    report.add(*data)
    if command not in DATA_CHECKED and needs is not None and needs.runtime != DOWNLOAD:
        report.add(
            Item(
                "coverage",
                "run-time library data",
                UNVERIFIED,
                required=False,
                detail=f"{command} also looks up reference-library data when it "
                "runs, which deps ready doesn't check yet; `wgsextract ref status` "
                "lists what is installed",
            )
        )
    if command in _FAMILY_TOOLS:
        report.add(*_family_items(command, args, data))


def _family_items(command: str, args: object, data: list[Item]) -> list[Item]:
    from wgsextract_cli.api._params import from_namespace

    reference = next(
        (item.path for item in data if item.kind == "reference" and item.path), None
    )
    if command == "vcf clair3":
        from wgsextract_cli.api.vcf import VcfClair3Params
        from wgsextract_cli.commands.vcf.clair3_readiness import clair3_items

        return clair3_items(from_namespace(VcfClair3Params, args))
    from wgsextract_cli.api.vcf import VcfDeepvariantParams
    from wgsextract_cli.commands.vcf.deepvariant_readiness import deepvariant_items

    return deepvariant_items(from_namespace(VcfDeepvariantParams, args), reference)
