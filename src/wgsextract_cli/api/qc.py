"""``qc``: quality reports for reads and variants, and fake test data.

``fastp`` trims reads; ``fastqc``, ``vcf`` and ``multiqc`` report on reads,
variants and earlier results. Outputs go to ``outdir``, else beside the
input."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core.builds import BUILD_CHOICES
from wgsextract_cli.core.messages import CLI_HELP
from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.table_writer import FORMATS, table_format_type
from wgsextract_cli.core.utils import UsageError

from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import (
    input_param,
    optional_input_param,
    outdir_param,
    ref_param,
    threads_param,
)


@dataclass(frozen=True, kw_only=True)
class FastpParams(Params):
    """Trim and filter reads with fastp into ``<reads>_fp_1.fastq.gz`` (and
    ``_fp_2`` for pairs) plus JSON and HTML reports. The read counts, Q30
    rate and duplication rate are metrics."""

    COMMAND = "qc fastp"

    r1: Path | None = param(
        None, help=CLI_HELP["arg_r1"], path="file", doc="Read 1 FASTQ (required)."
    )
    r2: Path | None = param(
        None, help=CLI_HELP["arg_r2"], path="file", doc="Read 2 FASTQ, for pairs."
    )
    outdir: Path | None = outdir_param("the R1 file's folder")
    threads: int | None = threads_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.r1 is None:
            # Optional on the command line, where --genome may find the reads.
            raise UsageError("--r1 is required unless --genome resolves FASTQ inputs.")


@dataclass(frozen=True, kw_only=True)
class FastqcParams(Params):
    """A FastQC report on a FASTQ, BAM or SAM file as ``<input>_fastqc.html``
    and ``.zip``. Module PASS/WARN/FAIL counts and the read count are metrics."""

    COMMAND = "qc fastqc"

    input: Path = input_param("FASTQ, BAM or SAM file.")
    outdir: Path | None = outdir_param()
    threads: int | None = threads_param()


def _reports_input(doc: str, required_message: str) -> Any:
    return param(
        doc=doc,
        flags=("--input", "-i"),
        path="file_or_directory",
        shared=True,
        required_message=required_message,
    )


@dataclass(frozen=True, kw_only=True)
class MultiqcParams(Params):
    """One MultiQC HTML report, ``multiqc_report.html``, from FastQC reports,
    ``bcftools stats`` files and WGS Extract result manifests. Runs offline,
    with MultiQC's AI summaries off."""

    COMMAND = "qc multiqc"

    input: Path = _reports_input(
        "A report or result manifest, or a folder searched for them.",
        "--input is required: choose FastQC reports, bcftools stats or result "
        "manifests.",
    )
    outdir: Path | None = outdir_param("the input folder, or the input's folder")
    force: bool = param(
        False,
        help="Replace an existing MultiQC report.",
        action="store_true",
        doc="Replace an existing report instead of failing.",
    )


@dataclass(frozen=True, kw_only=True)
class MultiqcMetricsParams(Params):
    """MultiQC custom-content files (``wgsextract_*_mqc.json``) from the
    metrics in WGS Extract result manifests; MultiQC isn't needed."""

    COMMAND = "qc multiqc-metrics"

    input: Path = _reports_input(
        "A result manifest, or a folder searched for them.",
        "--input is required: choose result manifests or a directory of them.",
    )
    outdir: Path | None = outdir_param("the input folder, or the input's folder")


@dataclass(frozen=True, kw_only=True)
class QcVcfParams(Params):
    """``bcftools stats`` on a VCF as ``<vcf>.vcfstats.txt``; record, SNP and
    indel counts and Ts/Tv are metrics. Set ``input`` or ``vcf_input``; the
    latter wins."""

    COMMAND = "qc vcf"

    input: Path | None = optional_input_param("VCF file.")
    vcf_input: Path | None = param(
        None, help=CLI_HELP["arg_vcf_input"], path="file", doc="VCF file."
    )
    outdir: Path | None = outdir_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.input is None and self.vcf_input is None:
            raise UsageError("--input is required.")


# The files of a VerifyBamID2 panel (``--svd-prefix``); .V is optional.
SVD_PANEL_SUFFIXES = (".UD", ".mu", ".bed", ".V")


@dataclass(frozen=True, kw_only=True)
class QcContaminationParams(Params):
    """Estimate how much of a BAM/CRAM's DNA comes from another person with
    VerifyBamID2: the FREEMIX fraction, from reads at 1000 Genomes sites
    (GRCh37/hg19 or GRCh38). Writes ``<stem>.contamination.selfSM`` and
    ``.Ancestry``. Metrics: ``freemix``, ``freemix_sites``, ``freemix_depth``
    and the ancestry PCs; the estimate is recorded as the sample's
    ``contamination`` fact. VerifyBamID2 is optional (``pixi install -e
    verifybamid2``) and unavailable on Windows."""

    COMMAND = "qc contamination"

    input: Path = input_param(
        "Indexed BAM or CRAM aligned to GRCh37/hg19 or GRCh38.",
        "--input (a BAM or CRAM file) is required.",
    )
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param()
    threads: int | None = threads_param()
    sites: str = param(
        "100k",
        help="1000 Genomes marker set: 100k (default) or 10k, faster and "
        "noisier, for low coverage",
        choices=("100k", "10k"),
    )
    svd_prefix: str | None = param(
        None,
        help="Your own VerifyBamID2 resource prefix (<prefix>.UD, .mu, .bed, "
        ".V), e.g. one built with --RefVCF for T2T; replaces --sites",
        doc="Your own VerifyBamID2 resource prefix (``<prefix>.UD``, ``.mu``, "
        "``.bed``, ``.V``); any build is then accepted. None uses the bundled "
        "1000 Genomes resources for the input's build.",
        metavar="PREFIX",
    )

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        # The panel files the prefix names; the server checks each one,
        # following symlinks, as it checks path fields.
        if self.svd_prefix:
            for suffix in SVD_PANEL_SUFFIXES:
                yield "svd_prefix", Path(self.svd_prefix + suffix)


@dataclass(frozen=True, kw_only=True)
class RelateParams(Params):
    """Sex, relatedness and continental ancestry with somalier.

    Each BAM, CRAM or VCF is sketched at ~17,800 common SNPs (``somalier
    extract``; the sketches are kept in ``<outdir>/somalier/`` and can be given
    again as inputs, so a relative's kit can be compared later without its
    BAM). The sketches are then compared pairwise (``somalier relate``) and,
    when the 1000 Genomes sketches are installed (``ref somalier --install``),
    placed in a superpopulation (``somalier ancestry``). Writes
    ``relate.samples`` and ``relate.pairs`` tables (TSV or Parquet) and
    somalier's HTML reports. Warns when the inferred sex disagrees with the
    sample's recorded sex, when two inputs are one person, and, with ``ped``,
    when a pair isn't related as the pedigree says (a swapped file). somalier
    is optional (``pixi install -e somalier``) and unavailable on Windows."""

    COMMAND = "qc relate"

    inputs: tuple[Path, ...] = param(
        help="BAM, CRAM or VCF files, or .somalier sketches from an earlier run.",
        metavar="FILE",
        path="file",
        positional=True,
        required_message="qc relate needs at least one input file.",
        doc="BAM, CRAM or VCF files, or .somalier sketches from an earlier run.",
    )
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param("the first input's folder")
    threads: int | None = threads_param()
    sites: Path | None = param(
        None,
        help="Sites VCF to genotype instead of somalier's own for the input's "
        "build (default: from 'ref somalier --install').",
        path="file",
    )
    ped: Path | None = param(
        None,
        help="PED/FAM file of the expected relationships and sexes; pairs or "
        "sexes that disagree are flagged as possible sample swaps.",
        path="file",
    )
    ancestry: bool | None = param(
        None,
        help="Predict the 1000 Genomes superpopulation (default: when 'ref "
        "somalier --install' has installed the labelled sketches).",
        action="boolean_optional",
    )
    table_format: str = param(
        "tsv",
        help="Table format: tsv, or parquet (needs pyarrow) (default: tsv)",
        flags=("--format",),
        choices=FORMATS,
        cli_type=table_format_type(),
    )

    def default_outdir(self) -> Path:
        """The first input's folder; for a sketch in an earlier run's
        ``somalier/`` folder, that run's folder."""
        first = self.inputs[0].expanduser().absolute()
        folder = first.parent
        if first.suffix == ".somalier" and folder.name == "somalier":
            return folder.parent
        return folder

    def write_folders(self) -> Iterator[Path]:
        outdir = self.outdir.expanduser() if self.outdir else self.default_outdir()
        yield from (outdir, outdir / "somalier")
        # An unindexed reference FASTA is indexed beside itself.
        ref = self.ref.expanduser() if self.ref else None
        if ref is not None and not ref.is_dir():
            yield ref.absolute().parent


@dataclass(frozen=True, kw_only=True)
class QcAncestryParams(Params):
    """Admixture-style proportions of 7 continental and 12 sub-continental
    HGDP+1KG reference components (not an ethnicity), with 95% intervals,
    from a BAM/CRAM, VCF or microarray file. Writes ``<stem>.ancestry.tsv``
    and the ``ancestry`` fact; see docs/ancestry.md."""

    COMMAND = "qc ancestry"

    input: Path = input_param(
        "Indexed BAM or CRAM, VCF, or microarray raw-data file.",
        "--input (a BAM, CRAM, VCF or microarray file) is required.",
    )
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param()
    threads: int | None = threads_param()
    sample: str | None = param(
        None,
        help="The sample of a multi-sample VCF",
        metavar="NAME",
    )
    build: str | None = param(
        None,
        help="The input's build, when its header doesn't tell",
        choices=("hg19", "hg38", "t2t"),
    )
    bootstrap: int = param(
        100,
        help="Bootstrap replicates for the 95%% intervals; 0 skips them",
        metavar="N",
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.bootstrap < 0:
            raise UsageError("--bootstrap must be 0 or more.")

    def write_folders(self) -> Iterator[Path]:
        # The first run downloads the reference into the library.
        from wgsextract_cli.core import ancestry_reference
        from wgsextract_cli.core.paths import expand_path

        from ._params import with_config_defaults

        ref = with_config_defaults(self).ref
        library = ancestry_reference.library_for(expand_path(ref) if ref else None)
        yield ancestry_reference.folder(library)


@dataclass(frozen=True, kw_only=True)
class FakeDataParams(Params):
    """Generate a small fake genome for tests: reference, reads and calls
    named ``fake*``, reproducible from ``seed``. Uses the build's installed
    reference when there is one."""

    COMMAND = "qc fake-data"

    coverage: float = param(1.0, help="Coverage depth (e.g. 30.0)")
    build: str = param(
        "hg38",
        help="Human genome build naming convention.",
        choices=tuple(BUILD_CHOICES),
    )
    types: str = param(
        "cram",
        help="Comma-separated list of types to generate (vcf, cram, bam, fastq, "
        "all). Default: cram",
        flags=("--type",),
        dest="type",
    )
    full_size: bool = param(
        False,
        help="Use real human chromosome lengths. The default scaled mode uses "
        "shorter chromosomes.",
        action="store_true",
    )
    legacy_bam: bool = param(
        False,
        help="Use the older scaled fake BAM generator. Slower and unavailable "
        "with --full-size, but includes randomized placement and indel CIGARs.",
        action="store_true",
    )
    seed: int = param(42, help="Random seed for reproducibility")
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param("the current folder")


def fastp(
    params: FastpParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Trim the reads (output roles ``trimmed_fastq_r1``, ``trimmed_fastq_r2``,
    ``fastp_report_json`` and ``fastp_report_html``)."""
    from wgsextract_cli.commands.qc.commands import run_fastp

    return execute(params, run_fastp, progress=progress, cancel=cancel, check=check)


def fastqc(
    params: FastqcParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Run FastQC (output roles ``fastqc_report_html`` and ``fastqc_report_zip``)."""
    from wgsextract_cli.commands.qc.commands import run_fastqc

    return execute(params, run_fastqc, progress=progress, cancel=cancel, check=check)


def multiqc(
    params: MultiqcParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Write the MultiQC report (output role ``multiqc_report``)."""
    from wgsextract_cli.commands.qc.commands import run_multiqc

    return execute(params, run_multiqc, progress=progress, cancel=cancel, check=check)


def multiqc_metrics(
    params: MultiqcMetricsParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Write the custom-content files (output role ``multiqc_custom_content``)."""
    from wgsextract_cli.commands.qc.commands import run_multiqc_metrics

    return execute(
        params, run_multiqc_metrics, progress=progress, cancel=cancel, check=check
    )


def vcf(
    params: QcVcfParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Write the VCF statistics (output role ``vcf_stats``)."""
    from wgsextract_cli.commands.qc.commands import run_vcf_qc

    if params.vcf_input is not None:
        # Only the chosen VCF is read, recorded and checked.
        params = dataclasses.replace(params, input=params.vcf_input, vcf_input=None)
    return execute(params, run_vcf_qc, progress=progress, cancel=cancel, check=check)


def contamination(
    params: QcContaminationParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Estimate contamination (output roles ``contamination_report`` and
    ``contamination_ancestry``)."""
    from wgsextract_cli.commands.qc.contamination import run_contamination

    return execute(
        params, run_contamination, progress=progress, cancel=cancel, check=check
    )


def relate(
    params: RelateParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Compare the inputs (see ``RelateParams``); output roles
    ``relate_samples``, ``relate_pairs``, ``somalier_sketch`` and
    ``somalier_report``."""
    from wgsextract_cli.commands.qc.relate import run_relate

    return execute(params, run_relate, progress=progress, cancel=cancel, check=check)


def ancestry(
    params: QcAncestryParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Estimate ancestry proportions (output role ``ancestry_table``)."""
    from wgsextract_cli.commands.qc.ancestry import run_ancestry

    return execute(params, run_ancestry, progress=progress, cancel=cancel, check=check)


def fake_data(
    params: FakeDataParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Generate the fake data (output roles ``reference``, ``alignment``,
    ``variants``, ``fastq_r1`` and ``fastq_r2``, by type)."""
    from wgsextract_cli.commands.qc import run_fake_data

    return execute(params, run_fake_data, progress=progress, cancel=cancel, check=check)


__all__ = [
    "FakeDataParams",
    "FastpParams",
    "FastqcParams",
    "MultiqcMetricsParams",
    "MultiqcParams",
    "QcAncestryParams",
    "QcContaminationParams",
    "QcVcfParams",
    "RelateParams",
    "ancestry",
    "contamination",
    "fake_data",
    "fastp",
    "fastqc",
    "multiqc",
    "multiqc_metrics",
    "relate",
    "vcf",
]
