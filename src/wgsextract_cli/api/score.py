"""Local polygenic sums, with explicit missingness and research-only labels.

Percentiles are withheld until score-specific ancestry calibration is available.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.utils import UsageError

from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import input_param, outdir_param


@dataclass(frozen=True, kw_only=True)
class ScorePgsParams(Params):
    """Score one sample's VCF on the specified build. Explicit calls and
    reference blocks are supported; a variants-only VCF needs a matching
    confident BED and reference FASTA to infer absent reference calls.
    Custom scoring files take precedence over Catalog selection.
    """

    COMMAND = "score pgs"

    input: Path = input_param("Single-sample VCF/gVCF, or choose --sample.")
    build: Literal["GRCh37", "GRCh38"] = param(
        help="Input/scoring coordinate build (must match).",
        choices=("GRCh37", "GRCh38"),
    )
    pgs_id: str | None = param(
        None,
        help="Catalog PGS ID; uncurated IDs carry a warning.",
        exclusive="selection",
    )
    trait: str | None = param(
        None, help="Curated trait (see docs/pgs.md).", exclusive="selection"
    )
    scoring_file: Path | None = param(
        None,
        help="Own scoring TSV[.gz], overriding Catalog selection.",
        path="file",
        config_key="pgs_scoring_file",
    )
    sample: str | None = param(
        None, help="VCF sample name (required for multi-sample files)."
    )
    plink2_check: bool = param(
        False,
        help="Cross-check the matched-set sum with installed plink2 (no imputation).",
        action="store_true",
    )
    keep_ambiguous: bool | None = param(
        None,
        help="Keep A/T and C/G SNPs only if their forward strand was independently verified.",
        action="boolean_optional",
        config_key="pgs_keep_ambiguous",
    )
    confident_bed: Path | None = param(
        None,
        help="Same-sample/build confident BED: absent SNPs within it are reference calls (needs --ref).",
        path="file",
    )
    ref: Path | None = param(
        None,
        help="Indexed reference FASTA, for confident BED/gVCF reference calls.",
        path="file",
        shared=True,
        config_key="reference_fasta",
    )
    min_match: float | None = param(
        None,
        help="Minimum matched fraction for percentile eligibility (default 0.75).",
        config_key="pgs_min_match",
        minimum=0,
    )
    outdir: Path | None = outdir_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.pgs_id and not re.fullmatch(r"PGS[0-9]{6}", self.pgs_id):
            raise UsageError("--pgs-id must look like PGS000018.")
        if self.pgs_id and self.trait:
            raise UsageError("Choose --pgs-id or --trait, not both.")
        if self.min_match is not None and (
            not math.isfinite(self.min_match) or not 0 <= self.min_match <= 1
        ):
            raise UsageError("--min-match must be a fraction between 0 and 1.")
        if self.confident_bed and not self.ref:
            raise UsageError("--confident-bed requires an indexed --ref FASTA.")


def pgs(
    params: ScorePgsParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Compute an additive score without imputing missing genotypes."""
    from wgsextract_cli.commands.score import run_pgs

    return execute(params, run_pgs, progress=progress, cancel=cancel, check=check)


__all__ = ["ScorePgsParams", "pgs"]
