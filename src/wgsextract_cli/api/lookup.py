"""``lookup``: one sample's genotype, evidence state and existing results at
an rsID, a build-qualified locus or a gene.

Reads only local files: the input VCF (variants-only or gVCF), its reference
evidence and the sample's personal report. Writes nothing::

    res = api.lookup.lookup(api.LookupParams(input=Path("s.vcf.gz"), rsid="rs429358"))
    res.details["site"]["status"]   # confident_call, confident_reference, ...

See docs/lookup.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.utils import UsageError

from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import (
    confidence_depth_param,
    confidence_gq_param,
    input_param,
    ref_param,
)

__all__ = ["LookupParams", "lookup"]

_BUILDS = ("GRCh37", "GRCh38", "CHM13v2.0")


@dataclass(frozen=True, kw_only=True)
class LookupParams(Params):
    """Look up one sample's genotype and evidence state at an rsID, a
    build-qualified locus or a gene, with what its personal report says
    there. Local files only; writes nothing."""

    COMMAND = "lookup"

    input: Path = input_param(
        "The sample's VCF/BCF: variants-only or a gVCF. Its gVCF (from --gvcf, "
        "or its run's manifest) tells confident reference from not assessed."
    )
    rsid: str | None = param(
        None,
        help="dbSNP rsID, e.g. rs429358 (merged IDs follow dbSNP build 157).",
        exclusive="query",
        metavar="RSID",
    )
    gene: str | None = param(
        None,
        help="Gene symbol or alias (NCBI RefSeq), e.g. APOE: its calls and "
        "evidence, paged by --limit.",
        exclusive="query",
        metavar="GENE",
    )
    locus: str | None = param(
        None,
        help="BUILD:CHROM:POS[-END][:REF>ALT], e.g. GRCh38:chr19:44908684:T>C; "
        "with --rsid or --gene, chooses one of several placements.",
        metavar="LOCUS",
    )
    sample: str | None = param(
        None, help="Sample of a multi-sample VCF (required for one)."
    )
    gvcf: Path | None = param(
        None,
        help="The sample's gVCF, if not the input or recorded by its run.",
        path="file",
    )
    report: Path | None = param(
        None,
        help="Personal report JSON (or its folder) to join (default: the one "
        "in the input's folder).",
        path="file_or_directory",
    )
    build: str | None = param(
        None,
        help="The input's build when its header doesn't show it.",
        choices=_BUILDS,
    )
    ref: Path | None = ref_param()
    limit: int = param(
        50, help="Calls per page of a gene or region (1-1000; default 50).", minimum=1
    )
    cursor: str | None = param(
        None, help="next_cursor of the previous page.", metavar="CURSOR"
    )
    confidence_depth: tuple[float, float] | None = confidence_depth_param()
    confidence_gq: tuple[float, float] | None = confidence_gq_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not (self.rsid or self.gene or self.locus):
            raise UsageError("Give --rsid, --locus or --gene.")
        if self.rsid and self.gene:
            raise UsageError("Give --rsid or --gene, not both.")


def lookup(
    params: LookupParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """``details`` is the ``wgsextract.lookup/1`` document: ``resolution``
    (and its ``candidates`` when the identifier is ambiguous), ``site`` or
    ``region`` (genotype, ``status`` and evidence state, calls),
    ``interpretation`` (report findings, section states) and ``resources``."""
    from wgsextract_cli.commands.lookup import run_lookup

    return execute(params, run_lookup, progress=progress, cancel=cancel, check=check)
