"""``panel bed``: a gene list or preset as a BED of target regions, for
Nanopore adaptive sampling (MinKNOW) or any targeted analysis::

    res = api.panel.bed(api.PanelBedParams(presets=("pharmacogenes",)))
    bed = res.outputs[0].path

Genes are padded (default 100 kb each side), clipped to their contig and
merged. The bundled NCBI RefSeq annotations give the spans; no network calls.

``panel check``: validate a research panel file (``wgsextract.panel/1``, TOML
or JSON) and resolve its genes, without sample data::

    res = api.panel.check(api.PanelCheckParams(panel=Path("my_panel.toml")))
    res.details["groups"]
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.panel import BUILDS
from wgsextract_cli.core.panel_presets import PRESETS
from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.utils import UsageError

from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import outdir_param, ref_param

__all__ = ["PanelBedParams", "PanelCheckParams", "bed", "check"]


@dataclass(frozen=True, kw_only=True)
class PanelBedParams(Params):
    """Write a BED of padded, merged gene regions for adaptive sampling."""

    COMMAND = "panel bed"

    genes: tuple[str, ...] = param(
        (),
        help="Gene symbols, comma-separated (repeatable), e.g. CYP2D6,HLA-A",
        flags=("--genes",),
        metavar="SYMBOLS",
        action="append",
    )
    gene_file: Path | None = param(
        None,
        help="File of gene symbols, one per line (or comma- or space-separated; "
        "# starts a comment)",
        flags=("--gene-file",),
        path="file",
        metavar="FILE",
    )
    presets: tuple[str, ...] = param(
        (),
        help="Gene preset (repeatable): " + ", ".join(PRESETS),
        flags=("--preset",),
        metavar="NAME",
        choices=tuple(PRESETS),
        action="append",
    )
    build: str | None = param(
        None,
        help="Coordinates of hg38 (GRCh38), t2t (CHM13v2.0) or hg19 (GRCh37) "
        "(default: --ref's build, else hg38)",
        flags=("--build",),
        choices=BUILDS,
    )
    pad_kb: float = param(
        100.0,
        help="Padding added to each side of every gene, in kb (default: 100)",
        flags=("--pad-kb",),
        metavar="KB",
        minimum=0,
    )
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param("the current folder")
    list_presets: bool = param(
        False,
        help="List the presets, their sources and genes; writes no files.",
        flags=("--list-presets",),
        action="store_true",
    )

    def __post_init__(self) -> None:
        for name in ("genes", "presets"):
            object.__setattr__(self, name, tuple(getattr(self, name) or ()))
        super().__post_init__()
        if not (self.list_presets or self.genes or self.gene_file or self.presets):
            raise UsageError("Give --genes, --gene-file or --preset.")


def bed(
    params: PanelBedParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Write the panel BED (output role ``panel_bed``). ``metrics`` hold the
    target count, bases and fraction of the genome."""
    from wgsextract_cli.commands.panel import run_panel_bed

    return execute(params, run_panel_bed, progress=progress, cancel=cancel, check=check)


@dataclass(frozen=True, kw_only=True)
class PanelCheckParams(Params):
    """Validate a research panel file and resolve its genes."""

    COMMAND = "panel check"

    panel: Path = param(
        help="Research panel file (wgsextract.panel/1, TOML or JSON)",
        flags=("--panel",),
        path="file",
        metavar="FILE",
    )
    build: str | None = param(
        None,
        help="Resolve genes in hg38 (GRCh38), t2t (CHM13v2.0) or hg19 (GRCh37) "
        "(default: the panel's build, else hg38)",
        flags=("--build",),
        choices=BUILDS,
    )


def check(
    params: PanelCheckParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Validate the panel (``details["valid"]``, ``details["problems"]``) and
    resolve its genes; writes no files. An invalid panel fails with error code
    ``panel_invalid`` and keeps the problems in ``details``."""
    from wgsextract_cli.commands._panel_check import run_panel_check

    return execute(
        params, run_panel_check, progress=progress, cancel=cancel, check=check
    )
