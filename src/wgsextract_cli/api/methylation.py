"""``methylation``: base modifications (5mC/5hmC) from long-read MM/ML tags.

``pileup`` runs ONT's modkit over a coordinate-sorted, indexed Nanopore or
PacBio BAM/CRAM whose reads carry MM/ML tags, against the reference they
were aligned to. It writes ``<input stem>.<context>.bedmethyl.gz`` (+ .tbi)
and a ``.methylation.tsv`` (or ``.parquet``) summary in ``outdir``, else
beside the input; with ``table_format="parquet"`` the sites are also written
as ``.bedmethyl.parquet``. The result's metrics give the platform, context
and, per modification, the sites, median coverage and weighted percentage
modified. modkit is optional (``pixi install -e modkit``) and unavailable on
Windows.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.methylation_annotations import BUILDS, IMPRINTED_DMRS, PRESETS
from wgsextract_cli.core.paths import absolute_path, append_suffix
from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.table_writer import FORMATS, table_format_type
from wgsextract_cli.core.utils import UsageError

from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import input_param, outdir_param, ref_param, threads_param


@dataclass(frozen=True, kw_only=True)
class MethylationPileupParams(Params):
    """Tabulate 5mC (and optionally 5hmC) per site with modkit (see the
    module docstring). ``context``, ``hydroxymethyl`` and ``combine_strands``
    default to config methylation_context, methylation_5hmc and
    methylation_combine_strands, else CpG, 5mC only and strands combined."""

    COMMAND = "methylation pileup"

    input: Path = input_param(
        "Coordinate-sorted, indexed BAM or CRAM with MM/ML tags.",
        required_message="--input (a BAM or CRAM file) is required.",
    )
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param()
    threads: int | None = threads_param()
    region: str | None = param(
        None,
        help="Region(s), 1-based and inclusive: chr20:10000000-11000000 or "
        "chr1,chr2 (default: the whole genome)",
        flags=("-r", "--region"),
    )
    context: str | None = param(
        None,
        help="cpg: CpG sites only; all: every cytosine (CHG/CHH too). Overrides "
        "methylation_context in config.toml (default: cpg).",
        choices=("cpg", "all"),
    )
    hydroxymethyl: bool | None = param(
        None,
        help="Also report 5-hydroxymethylcytosine (Nanopore 5mCG_5hmCG models). "
        "Overrides methylation_5hmc in config.toml (default: 5mC only).",
        flags=("--5hmc",),
        action="boolean_optional",
    )
    combine_strands: bool | None = param(
        None,
        help="Sum both strands of each CpG into one row on its C. Overrides "
        "methylation_combine_strands in config.toml (default: on for CpG).",
        doc="Sum both strands of each CpG into one row on its C. True with "
        "context 'all' is an error; the config default quietly gives way.",
        action="boolean_optional",
    )
    table_format: str = param(
        "tsv",
        help="Summary table format: tsv, or parquet (needs pyarrow), which also "
        "writes the sites as <stem>.<context>.bedmethyl.parquet (default: tsv)",
        flags=("--format",),
        choices=FORMATS,
        cli_type=table_format_type(),
    )

    def write_folders(self) -> Iterator[Path]:
        # An unindexed reference FASTA is indexed beside itself.
        ref = self.ref.expanduser() if self.ref else None
        if ref is not None and not ref.is_dir():
            yield ref.absolute().parent


def pileup(
    params: MethylationPileupParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Tabulate base modifications (see ``MethylationPileupParams``)."""
    from wgsextract_cli.commands.methylation import run_pileup

    return execute(params, run_pileup, progress=progress, cancel=cancel, check=check)


@dataclass(frozen=True, kw_only=True)
class MethylationRegionsParams(Params):
    """Summarize pooled or haplotype-split CpG calls over annotated regions."""

    COMMAND = "methylation regions"

    input: Path = input_param(
        "Sorted, strand-combined CpG bedMethyl, or indexed HP-tagged BAM/CRAM with --partition-hp."
    )
    ref: Path = param(
        doc="The indexed, local FASTA used to align the reads or make the bedMethyl.",
        path="file",
        shared=True,
        config_key="reference_fasta",
        required_message="--ref must name the indexed FASTA matching --input.",
    )
    outdir: Path | None = outdir_param()
    build: str = param(choices=BUILDS, help="Assembly of both input and FASTA.")
    preset: str = param(
        choices=PRESETS,
        help="RefSeq promoters, UCSC masked CpG islands, or research-only imprinted DMRs (hg38 only).",
    )
    upstream: int = param(
        2000, minimum=0, help="Promoter bases upstream of the TSS (default: 2000)."
    )
    downstream: int = param(
        500, minimum=1, help="Promoter bases on the transcribed side (default: 500)."
    )
    min_valid_calls: int = param(
        5, minimum=1, help="Minimum valid calls per CpG (default: 5)."
    )
    min_cpgs: int = param(
        3, minimum=1, help="Minimum qualifying CpGs per region (default: 3)."
    )
    partition_hp: bool = param(
        False,
        action="store_true",
        help="Split CpG calls from HP-tagged BAM/CRAM (needs modkit on Linux or macOS).",
    )
    phase_blocks: Path | None = param(
        None,
        path="file",
        help="Matching phased.blocks.tsv from vcf phase; require region containment in one block.",
    )
    threads: int | None = threads_param()
    hydroxymethyl: bool = param(
        False,
        flags=("--5hmc",),
        action="store_true",
        help="Also report 5hmC: declares the input was generated with a 5hmC-capable model.",
    )
    download: bool = param(
        False,
        action="store_true",
        writes=True,
        local_only=True,
        help="Fetch the missing pinned hg38 CpG island table from UCSC. No sample data is sent.",
    )
    table_format: str = param(
        "tsv",
        flags=("--format",),
        choices=FORMATS,
        cli_type=table_format_type(),
        help="Output table: tsv (default) or parquet (needs pyarrow).",
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.preset == IMPRINTED_DMRS and self.build != "hg38":
            raise UsageError(
                "--preset imprinted-dmrs requires GRCh38: use --build hg38 with "
                "GRCh38 input and its matching FASTA, or choose a different preset. "
                "Coordinates are not lifted over."
            )
        alignment = self.input.suffix.lower() in (".bam", ".cram")
        if self.partition_hp and not alignment:
            raise UsageError(
                "--partition-hp requires an indexed HP-tagged BAM or CRAM."
            )
        if alignment and not self.partition_hp:
            raise UsageError("BAM or CRAM input requires --partition-hp.")
        if self.phase_blocks is not None and not self.partition_hp:
            raise UsageError("--phase-blocks requires --partition-hp.")
        if self.preset != "promoters" and (self.upstream, self.downstream) != (
            2000,
            500,
        ):
            raise UsageError("--upstream and --downstream only apply to promoters.")

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        from wgsextract_cli.core.methylation_annotations import hg38_island_path

        for name, path in (
            ("input", self.input),
            ("ref", self.ref),
            ("outdir", self.outdir),
            ("phase_blocks", self.phase_blocks),
        ):
            if path is not None and ".." in path.parts:
                yield name, absolute_path(path)
        ref = absolute_path(self.ref) if ".." in self.ref.parts else self.ref
        yield "ref", append_suffix(ref, ".fai")
        yield "ref", append_suffix(ref, ".gzi")
        if self.partition_hp:
            from wgsextract_cli.core.alignment.samtools_commands import alignment_index

            if index := alignment_index(self.input):
                yield "input", absolute_path(index)
        if self.build == "hg38" and self.preset == "cpg-islands":
            yield "annotation", hg38_island_path()


def regions(
    params: MethylationRegionsParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Summarize methylation over the selected build-matched regions."""
    from wgsextract_cli.commands.methylation_regions import run_regions

    return execute(params, run_regions, progress=progress, cancel=cancel, check=check)


__all__ = ["MethylationPileupParams", "MethylationRegionsParams", "pileup", "regions"]
