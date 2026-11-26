from __future__ import annotations

import argparse

from wgsextract_cli.api._cli import add_arguments
from wgsextract_cli.api.score import ScorePgsParams
from wgsextract_cli.cli._lazy import command_handler
from wgsextract_cli.commands._certification import REPO, experimental
from wgsextract_cli.commands._command_needs import (
    MINUTES,
    _n,
)

CERTIFICATION = {
    "score pgs": experimental(
        "2026-11-25",
        "Research-only polygenic scores; fixture checks are not clinical calibration.",
        f"{REPO}/blob/main/tests/test_pgs.py",
    ),
}

NEEDS = {
    "score pgs": _n(MINUTES, "bcftools", optional="plink2 curl"),
}


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "score", help="Local research-only polygenic scores."
    )
    subs = parser.add_subparsers(dest="score_cmd", required=True)
    sub = subs.add_parser(
        "pgs",
        parents=[base_parser],
        help="Score an additive PGS Catalog or custom file locally.",
    )
    add_arguments(sub, ScorePgsParams)
    sub.set_defaults(
        func=command_handler("wgsextract_cli.commands.score", "run_pgs", ScorePgsParams)
    )
