"""Local, callability-aware cohort summaries for research."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.table_writer import FORMATS, table_format_type

from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import outdir_param


@dataclass(frozen=True, kw_only=True)
class CohortSummarizeParams(Params):
    """Summarize autosomal SNVs from a local cohort manifest and matched gVCFs."""

    COMMAND = "cohort summarize"
    # The server cannot path-check files named inside a client-supplied manifest.
    LOCAL_ONLY = True
    PRIVATE_PROVENANCE = True
    SAVES_IN_WORKING_FOLDER = True

    cohort_manifest: Path = param(
        help="Local cohort input manifest (wgsextract.cohort/1).",
        path="file",
        metavar="MANIFEST",
        positional=True,
    )
    region: str | None = param(
        None, help="Limit to an autosomal contig, interval or comma-separated list."
    )
    bed: Path | None = param(
        None, help="Limit to autosomal intervals from a local BED.", path="file"
    )
    min_depth: int = param(10, minimum=1, help="Minimum DP (block MIN_DP).")
    min_gq: int = param(20, minimum=1, help="Minimum GQ for callers that provide it.")
    table_format: str = param(
        "tsv",
        help="Table format: tsv or parquet (needs pyarrow).",
        flags=("--format",),
        choices=FORMATS,
        cli_type=table_format_type(),
    )
    include_identifiers: bool = param(
        False,
        help="Also write a private participant/QC join with source paths and names.",
        action="store_true",
    )
    outdir: Path | None = outdir_param("the working folder")


def summarize(
    params: CohortSummarizeParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Write a local allele-count table and a result manifest."""
    from wgsextract_cli.commands.cohort import run_summary

    return execute(params, run_summary, progress=progress, cancel=cancel, check=check)
