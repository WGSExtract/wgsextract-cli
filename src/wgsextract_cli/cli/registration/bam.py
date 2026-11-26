from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.bam import (
    BamIdentifyParams,
    BamIndexParams,
    BamMergeParams,
    BamSortParams,
    BamToBamParams,
    BamToCramParams,
    BamToUbamParams,
    BamUnalignParams,
    BamUnindexParams,
    BamUnsortParams,
)
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import tested
from wgsextract_cli.commands._command_needs import (
    _SAM,
    HOURS,
    INSTANT,
    MINUTES,
    SECONDS,
    _n,
)
from wgsextract_cli.commands._messages.bam import CLI_HELP

_REVIEWED = "2026-11-25"
CERTIFICATION = {
    "bam sort": tested(
        _REVIEWED, "test_bam_commands.py", "Sort order checked on fixture alignments."
    ),
    "bam index": tested(
        _REVIEWED,
        "test_bam_commands.py",
        "Index creation checked on fixture alignments.",
    ),
    "bam unindex": tested(
        _REVIEWED,
        "test_bam_commands.py",
        "Index removal checked on fixture alignments.",
    ),
    "bam unsort": tested(
        _REVIEWED,
        "test_bam_commands.py",
        "BAM and CRAM sort-order headers are relabeled without changing any records.",
    ),
    "bam to-cram": tested(
        _REVIEWED,
        "test_cram_reference.py",
        "CRAM conversion and reference handling checked on fixtures.",
    ),
    "bam to-bam": tested(
        _REVIEWED,
        "test_bam_commands.py",
        "BAM conversion checked on fixture alignments.",
    ),
    "bam unalign": tested(
        _REVIEWED,
        "test_bam_unalign_paths.py",
        "FASTQ output checked on fixture alignments.",
    ),
    "bam identify": tested(
        _REVIEWED,
        "test_bam_commands.py",
        "Build identification checked on fixture alignments.",
    ),
    "bam merge": tested(
        _REVIEWED,
        "test_bam_merge_runs.py",
        "Merge and publication checked on fixture alignments.",
    ),
    "bam to-ubam": tested(
        _REVIEWED,
        "test_bam_to_ubam.py",
        "Unmapped BAM conversion checked on fixture alignments.",
    ),
}

NEEDS = {
    "bam sort": _n(HOURS, *_SAM, optional="bcftools sambamba"),
    "bam index": _n(MINUTES, *_SAM, optional="sambamba"),
    "bam unindex": _n(INSTANT, *_SAM),
    "bam unsort": _n(HOURS, *_SAM, optional="bcftools"),
    "bam to-cram": _n(MINUTES, *_SAM, optional="bcftools sambamba"),
    "bam to-bam": _n(HOURS, *_SAM, optional="bcftools sambamba"),
    "bam unalign": _n(HOURS, *_SAM, optional="bcftools"),
    "bam identify": _n(SECONDS, *_SAM, optional="bcftools", scale=""),
    "bam merge": _n(HOURS, *_SAM, optional="bcftools"),
    "bam to-ubam": _n(HOURS, *_SAM, optional="bcftools"),
}


cmd_identify = command_handler("wgsextract_cli.commands.bam", "cmd_identify")
cmd_index = command_handler("wgsextract_cli.commands.bam", "cmd_index")
cmd_merge = command_handler("wgsextract_cli.commands.bam", "cmd_merge")
cmd_sort = command_handler("wgsextract_cli.commands.bam", "cmd_sort")
cmd_to_ubam = command_handler("wgsextract_cli.commands._bam_ubam", "cmd_to_ubam")
cmd_tobam = command_handler("wgsextract_cli.commands.bam", "cmd_tobam")
cmd_tocram = command_handler("wgsextract_cli.commands.bam", "cmd_tocram")
cmd_unalign = command_handler("wgsextract_cli.commands.bam", "cmd_unalign")
cmd_unindex = command_handler("wgsextract_cli.commands.bam", "cmd_unindex")
cmd_unsort = command_handler("wgsextract_cli.commands.bam", "cmd_unsort")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser("bam", help=CLI_HELP["cmd_bam_mgmt"])
    bam_subs = parser.add_subparsers(dest="bam_cmd", required=True)
    for name, cls, func, help_key in (
        ("sort", BamSortParams, cmd_sort, "cmd_sort"),
        ("index", BamIndexParams, cmd_index, "cmd_index"),
        ("unindex", BamUnindexParams, cmd_unindex, "cmd_unindex"),
        ("unsort", BamUnsortParams, cmd_unsort, "cmd_unsort"),
        ("to-cram", BamToCramParams, cmd_tocram, "cmd_to-cram"),
        ("to-bam", BamToBamParams, cmd_tobam, "cmd_to-bam"),
        ("unalign", BamUnalignParams, cmd_unalign, "cmd_unalign"),
        ("identify", BamIdentifyParams, cmd_identify, "cmd_bam-identify"),
        ("merge", BamMergeParams, cmd_merge, "cmd_bam-merge"),
        ("to-ubam", BamToUbamParams, cmd_to_ubam, "cmd_bam-to-ubam"),
    ):
        sub = bam_subs.add_parser(name, parents=[base_parser], help=CLI_HELP[help_key])
        add_arguments(sub, cls)
        sub.set_defaults(func=func)
