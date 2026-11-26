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

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core.panel import BUILDS
from wgsextract_cli.core.panel_presets import PRESETS
from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.utils import UsageError

from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import outdir_param, ref_param

__all__ = [
    "PanelAuditParams",
    "PanelBedParams",
    "PanelCheckParams",
    "audit",
    "bed",
    "check",
]


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


@dataclass(frozen=True, kw_only=True)
class PanelAuditParams(PanelCheckParams):
    """Apply an opt-in research panel to one annotated genome, offline."""

    COMMAND = "panel audit"

    vcf_input: Path = param(
        help="Local single-sample annotated VCF, gVCF or BCF",
        flags=("--vcf-input",),
        path="file",
        metavar="VCF",
    )
    research_panel: bool = param(
        False,
        help="Acknowledge that results are for research only, not a diagnosis.",
        flags=("--research-panel",),
        action="store_true",
    )
    gvcf: Path | None = param(
        None,
        help="Local gVCF of the same sample (default: input or companion gVCF)",
        flags=("--gvcf",),
        path="file",
        metavar="GVCF",
    )
    exons: Path | None = param(
        None,
        help="Local exon model like `vcf callability --exons`",
        flags=("--exons",),
        path="file",
        metavar="FILE",
    )
    null_sets: int | None = param(
        None,
        help="Run calibration with this many null sets (100 to 100,000).",
        flags=("--null-sets",),
        minimum=100,
    )
    seed: int | None = param(
        None,
        help="Reproducible seed for null calibration.",
        flags=("--seed",),
        minimum=0,
    )
    no_calibration: bool = param(
        False,
        help="Skip null calibration, even if the panel requests it.",
        flags=("--no-calibration",),
        action="store_true",
    )
    download: bool = param(
        False,
        help="Allow fetching the missing pinned public GRCh38 exon model from "
        "WGSExtract's GitHub release; sends no sample data.",
        flags=("--download",),
        action="store_true",
    )
    ref: Path | None = ref_param(local_only=True)
    table_format: str = param(
        "tsv",
        help="Audit table format (default: tsv)",
        flags=("--format",),
        choices=("tsv", "parquet"),
    )
    outdir: Path | None = outdir_param()

    def _calibration_override(self) -> bool:
        return self.null_sets is not None or self.seed is not None or self.download

    def requests_calibration(self, spec: Mapping[str, Any]) -> bool:
        return not self.no_calibration and (
            "calibration" in spec or self._calibration_override()
        )

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        yield from super().implied_paths()
        if self.exons is not None or self.no_calibration:
            return
        from wgsextract_cli.core.panel_reference import (
            mane_model_path,
            mane_staging_paths,
        )
        from wgsextract_cli.core.panel_spec import read_panel

        # The server checks the panel path before inspecting its calibration opt-in.
        panel = self.panel.expanduser()
        try:
            spec = read_panel(panel).document if panel.is_file() else None
        except UsageError:
            spec = None  # The worker reports the panel read error.
        if not self.requests_calibration(spec or {}):
            return
        path = mane_model_path(self.ref)
        yield "ref", path.parent
        yield "ref", path
        if self.download:
            for staging_path in mane_staging_paths(path):
                yield "ref", staging_path

    def write_folders(self) -> Iterator[Path]:
        yield from super().write_folders()
        if self.download and self.exons is None:
            from wgsextract_cli.core.panel_reference import mane_model_path

            yield mane_model_path(self.ref).parent

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.research_panel:
            raise UsageError("Give --research-panel to acknowledge research-only use.")
        if self.null_sets is not None and self.null_sets > 100000:
            raise UsageError("--null-sets must be at most 100,000.")
        if self.seed is not None and self.seed > 2**53:
            raise UsageError("--seed must be at most 2**53.")
        if self.no_calibration and self._calibration_override():
            raise UsageError(
                "--no-calibration cannot be used with --null-sets, --seed or --download."
            )


def audit(
    params: PanelAuditParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Write research-only panel audit JSON and a variant table."""
    from wgsextract_cli.commands._panel_audit import run_panel_audit

    return execute(
        params, run_panel_audit, progress=progress, cancel=cancel, check=check
    )
