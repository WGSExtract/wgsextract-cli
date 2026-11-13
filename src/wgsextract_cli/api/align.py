"""``basecall``, ``align`` and ``realign``: turn Nanopore signal into reads,
and map reads to a reference as a sorted, indexed, duplicate-marked BAM or
CRAM.

Short reads go through bwa-mem2, bwa or bwa-mem3 and samblaster, long reads through
pbmm2 (PacBio) or minimap2, and Ultima reads through their own pipeline. The
output is named after the reads (``sample_R1.fastq.gz`` and
``sample_R2.fastq.gz`` give ``sample_aligned.bam``) and written to
``outdir``, else beside the reads."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core.messages import CLI_HELP
from wgsextract_cli.core.native_aligners import SETTINGS as NATIVE_ALIGNER_SETTINGS
from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.utils import UsageError

from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import (
    dry_run_param,
    force_steps_param,
    input_param,
    memory_param,
    no_gpu_memory_check_param,
    optional_input_param,
    outdir_param,
    ref_param,
    restart_param,
    threads_param,
)

PLATFORMS = ("illumina", "dnbseq", "ultima", "pacbio", "hifi", "clr", "ont", "nanopore")
ALIGNERS = ("auto", "bwa", "bwa-mem2", "bwa-mem3", "minimap2", "pbmm2")
PRESETS = ("sr", "map-pb", "map-hifi", "map-ont", "ccs", "subread")
REALIGN_STEPS = ("extract", "align", "index")


def _native_aligners_param() -> Any:
    return param(
        None,
        help="On Apple Silicon, align with wgse-bwa, wgse-bwa-mem2 or "
        "wgse-minimap2 in place of bwa, bwa-mem2 or minimap2, and with "
        "wgse-bwa-mem3 in place of bwa or bwa-mem2 when the reference has "
        "bwa-mem2's index and it fits in RAM; on Linux (x86-64 and ARM64), with "
        "wgse-bwa in place of bwa, and on x86-64 with wgse-bwa-mem3 as on Apple "
        "Silicon and wgse-minimap2 in place of minimap2 (long reads' gap fills on "
        "the NVIDIA GPU). Used when they're on PATH (auto; same alignments, "
        "faster, on the Metal or NVIDIA GPU when it fits) or never (off). "
        "On Linux without wgse-bwa-mem3, auto uses Bioconda's bwa-mem3 (`pixi "
        "install -e bwa-mem3`) in its place, ahead of wgse-bwa. Default: config native_aligners, else "
        "auto. Install the builds as native/README.md describes.",
        doc="auto (the default, or config native_aligners) uses WGS Extract's "
        "aligner builds (Apple Silicon; wgse-bwa on Linux, and wgse-bwa-mem3 and "
        "wgse-minimap2 on x86-64) when installed on PATH, and on Linux without wgse-bwa-mem3 "
        "Bioconda's bwa-mem3 in place of bwa or bwa-mem2; "
        "off never does.",
        choices=NATIVE_ALIGNER_SETTINGS,
        config_key="native_aligners",
    )


def _dense_suffix_array_param() -> Any:
    return param(
        None,
        help="Have wgse-bwa (0.11.0 or later) read the reference's dense "
        "suffix-array file, built by `ref dense-sa`: the same alignments, "
        "faster, holding the file in RAM (0.8 GB for hs38). Used only when "
        "wgse-bwa aligns: otherwise (another aligner, such as wgse-bwa-mem3 "
        "standing in, no file, or no such wgse-bwa) the run warns "
        "(dense_suffix_array_unused) and aligns without it. Default: config "
        "dense_suffix_array, else off.",
        doc="Opt in to wgse-bwa's dense suffix-array file (``ref dense-sa``): "
        "the same alignments, faster, with the file held in RAM. None uses "
        "config dense_suffix_array, else off. Used only when wgse-bwa aligns; "
        "otherwise (another aligner, no file, or no wgse-bwa that reads it) "
        "the run warns (dense_suffix_array_unused).",
        action="boolean_optional",
        config_key="dense_suffix_array",
    )


GPU_HELP = (
    "Align on an NVIDIA GPU with Parabricks: BWA-MEM for short reads, "
    "minimap2 for HiFi (Linux, Docker; see README). ONT requires CPU "
    "alignment to retain MM/ML methylation tags."
)
_FORMAT_DOC = "Output format: BAM or CRAM (written with the target reference)."


def _fast_param() -> Any:
    return param(
        False,
        help="Align short reads with bwa-mem3's --fast speed preset (implies "
        "--aligner bwa-mem3), about twice as fast. Its alignments differ "
        "from BWA's (docs/hardware.md has a measurement); the run warns "
        "(bwa_mem3_fast). Without it bwa-mem3 writes BWA's exact records.",
        action="store_true",
    )


@dataclass(frozen=True, kw_only=True)
class AlignParams(Params):
    """Align FASTQ reads (or a BAM/CRAM of long or Ultima reads) to ``ref`` as ``<reads stem>_aligned.bam`` (or ``.cram``) with its index.
    ``r1`` is required; the CLI's ``--genome`` may fill it in from a sample
    workspace."""

    COMMAND = "align"

    r1: Path | None = param(
        None,
        help=CLI_HELP["arg_r1"],
        doc=(
            "Read 1 FASTQ (or FASTA of long reads), or a BAM/CRAM of long reads "
            "(PacBio is detected) or of Ultima reads. Short reads in a BAM/CRAM "
            "go through realign."
        ),
        path="file",
    )
    r2: Path | None = param(
        None, help=CLI_HELP["arg_r2"], doc="Read 2 FASTQ, for pairs.", path="file"
    )
    preset: str | None = param(
        None,
        help="minimap2 preset for non-BWA alignment. Defaults to map-hifi for "
        "PacBio and map-ont for ONT.",
        choices=PRESETS,
    )
    platform: str | None = param(
        None,
        help="Sequencing platform; selects platform-specific alignment tooling "
        "(default: detected from read groups or read names, else illumina).",
        choices=PLATFORMS,
    )
    aligner: str = param(
        "auto",
        help="Aligner to use. Auto uses bwa-mem2 for short reads when the reference "
        "has its index (see `ref index`), else bwa; for long reads pbmm2 for PacBio "
        "when installed, otherwise minimap2. bwa-mem3 (wgse-bwa-mem3 on Apple "
        "Silicon and Linux x86-64, else bwa-mem3 from PATH or its Pixi environment) writes bwa's exact records faster, "
        "from bwa-mem2's index.",
        choices=ALIGNERS,
    )
    fast: bool = _fast_param()
    native_aligners: str | None = _native_aligners_param()
    dense_suffix_array: bool | None = _dense_suffix_array_param()
    gpu: bool = param(False, help=GPU_HELP, action="store_true")
    no_gpu_memory_check: bool = no_gpu_memory_check_param()
    sample: str | None = param(
        None, help="Sample name for the read group (default: the reads' file name)."
    )
    long_read: bool = param(
        False,
        help=CLI_HELP["arg_long_read"],
        doc="Treat the reads as long reads (PacBio/ONT) and align them with "
        "pbmm2 or minimap2.",
        action="store_true",
    )
    format: str = param(
        "BAM", help="Output format", doc=_FORMAT_DOC, choices=("BAM", "CRAM")
    )
    input: Path | None = optional_input_param(
        "An aligned BAM/CRAM of the same sample whose header picks the "
        "reference from the library when ref is unset."
    )
    ref: Path | None = ref_param()
    input_ref: Path | None = param(
        None,
        help="Reference FASTA a CRAM --r1 was encoded with (default: --ref when "
        "its sequence names, lengths and MD5s match the CRAM).",
        path="file",
    )
    outdir: Path | None = outdir_param("the input's folder, else the reads'")
    threads: int | None = threads_param()
    memory: str | None = memory_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.r1 is None:
            # Optional on the command line, where --genome may find the reads.
            raise UsageError("--r1 is required unless --genome resolves read inputs.")


@dataclass(frozen=True, kw_only=True)
class RealignParams(Params):
    """Re-align a BAM/CRAM to ``ref`` (possibly a different build) as
    ``<input stem>_aligned.bam`` (or ``.cram``) with its index.

    The reads are extracted to FASTQ first, or streamed into the aligner when
    the machine has the memory; FASTQs from an earlier extraction (beside the
    input or in ``outdir``) or given as ``r1``/``r2`` are reused. Single-end
    ONT and PacBio reads (detected, or ``platform``) stream each read's primary
    record with its MM/ML tags into minimap2. Free disk
    space is checked first. Completed steps (extract, align, index) are
    recorded in ``<output>.steps.json`` and skipped on a rerun whose inputs,
    settings and tools are unchanged."""

    COMMAND = "realign"

    r1: Path | None = param(
        None,
        help=CLI_HELP["arg_r1"],
        doc="Read 1 FASTQ already extracted from the input, to align instead "
        "of extracting it.",
        path="file",
    )
    r2: Path | None = param(
        None, help=CLI_HELP["arg_r2"], doc="Read 2 FASTQ, with r1.", path="file"
    )
    aligner: str = param(
        "auto",
        help="Aligner to use (matches the 'align' command).",
        doc="Aligner: auto, bwa, bwa-mem2, bwa-mem3, minimap2 or pbmm2, chosen "
        "as for align.",
        choices=ALIGNERS,
    )
    fast: bool = _fast_param()
    native_aligners: str | None = _native_aligners_param()
    dense_suffix_array: bool | None = _dense_suffix_array_param()
    gpu: bool = param(False, help=GPU_HELP, action="store_true")
    no_gpu_memory_check: bool = no_gpu_memory_check_param()
    platform: str | None = param(
        None,
        help="Sequencing platform (default: detected from read groups or read "
        "names, else illumina).",
        choices=PLATFORMS,
    )
    preset: str | None = param(None, help="minimap2/pbmm2 preset.", choices=PRESETS)
    input_ref: Path | None = param(
        None,
        help="Reference FASTA the input CRAM was encoded with (default: --ref "
        "when its sequence names, lengths and MD5s match the CRAM, else the "
        "FASTA in the reference library whose sequence dictionary matches). "
        "--ref is the reference to realign to.",
        path="file",
    )
    sample: str | None = param(
        None, help="Sample name to embed in read-group metadata."
    )
    long_read: bool = param(
        False, help="Treat input as long-read data (PacBio/ONT).", action="store_true"
    )
    format: str = param(
        "BAM", help="Output format.", doc=_FORMAT_DOC, choices=("BAM", "CRAM")
    )
    keep_fastq: bool = param(
        False,
        help="Keep the FASTQs extracted from --input after alignment (default: "
        "delete them); ONT and PacBio reads are then extracted to one tagged "
        "<input stem>.fastq.gz. FASTQs passed with --r1/--r2 or reused from an earlier "
        "extraction are never deleted.",
        action="store_true",
    )
    force_extract: bool = param(
        False,
        help="Re-extract FASTQs even if a matching pair already exists "
        "next to the BAM.",
        action="store_true",
    )
    chunk_size: str = param(
        "4G",
        help="Realign ONT and PacBio reads in chunks of about this much of "
        "the input (K, M, G or T; 0: in one pass). Temporary files then stay "
        "chunk-sized and an interrupted run resumes at its first unfinished "
        "chunk. Needs a coordinate-sorted, indexed input; ignored with "
        "--keep-fastq.",
    )
    no_preflight: bool = param(
        False,
        help="Skip the up-front disk space check (not recommended).",
        action="store_true",
    )
    restart: bool = restart_param()
    force_steps: tuple[str, ...] | None = force_steps_param(REALIGN_STEPS)
    dry_run: bool = dry_run_param()
    input: Path | str = input_param(
        "The aligned BAM or CRAM to realign. ONT and PacBio reads may also be "
        "an http(s) URL of one (command line and Python only), streamed "
        "without a local copy; its index is downloaded beside the output.",
        required_message="--input <BAM|CRAM> is required for realign. Pass it "
        "before the subcommand or via --input.",
        url=True,
    )
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param()
    threads: int | None = threads_param()
    memory: str | None = memory_param()


DORADO_MODELS = ("fast", "hac", "sup")


@dataclass(frozen=True, kw_only=True)
class BasecallParams(Params):
    """Basecall Nanopore POD5 signal with ONT's Dorado into an unaligned BAM,
    ``<input stem>.<model>[.<modifications>].bam``, that keeps the MM/ML
    base-modification tags and feeds ``align`` (``r1``) directly. Dorado is
    an optional tool the user installs; models download into
    ``models_dir``."""

    COMMAND = "basecall"

    input: Path = param(
        doc="A POD5 file, or a folder searched (recursively) for them.",
        flags=("--input", "-i"),
        path="file_or_directory",
        shared=True,
        required_message="--input <POD5 file or folder> is required.",
    )
    model: str = param(
        "hac",
        help="Dorado model: fast, hac or sup (optionally pinned, e.g. "
        "sup@v5.2.0; picked for the run's chemistry), or a model folder "
        "(default: hac)",
        metavar="MODEL",
        may_name_file=True,
    )
    modified_bases: str | None = param(
        None,
        help="Also call base modifications, e.g. 5mCG_5hmCG (CpG methylation) "
        "or 5mC_5hmC; comma-separated for several. Writes MM/ML tags.",
        metavar="CODES",
    )
    device: str = param(
        "auto",
        help="auto (Apple silicon GPU, else an NVIDIA GPU nvidia-smi lists, "
        "else CPU), cpu, metal, cuda:all or cuda:<id>[,<id>...] (default: auto)",
        metavar="DEVICE",
    )
    read_ids: Path | None = param(
        None,
        help="Basecall only the reads named in this file (one read ID per line).",
        path="file",
        metavar="FILE",
    )
    regions_bed: Path | None = param(
        None,
        help="Re-basecall only the reads --alignment places in these regions "
        "(a panel BED), e.g. with --model sup.",
        path="file",
        metavar="BED",
    )
    alignment: Path | None = param(
        None,
        help="Aligned BAM/CRAM of the same run whose reads in --regions-bed "
        "are re-basecalled.",
        path="file",
        metavar="BAM",
    )
    models_dir: Path | None = param(
        None,
        help="Folder Dorado downloads models into and reuses them from "
        "(default: config dorado_models_directory, else the wgsextract cache's "
        "dorado-models)",
        path="directory",
        config_key="dorado_models_directory",
        local_only=True,
    )
    dorado_path: str | None = param(
        None,
        help="Path to the dorado executable (optional if in PATH)",
        config_key="dorado_executable",
        local_only=True,
    )
    ref: Path | None = ref_param()
    outdir: Path | None = outdir_param(
        "the POD5 file's folder, or the POD5 folder's parent"
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.read_ids and (self.regions_bed or self.alignment):
            raise UsageError("Give --read-ids or --regions-bed, not both.")
        if bool(self.regions_bed) != bool(self.alignment):
            raise UsageError("--regions-bed and --alignment go together.")

    def write_folders(self) -> Iterator[Path]:
        from wgsextract_cli.commands.basecall import models_directory

        yield models_directory(self.models_dir)


def basecall(
    params: BasecallParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Basecall POD5 signal into an unaligned BAM (output role
    ``unaligned_reads``); see ``BasecallParams``."""
    from wgsextract_cli.commands.basecall import run_basecall

    return execute(params, run_basecall, progress=progress, cancel=cancel, check=check)


def align(
    params: AlignParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Align reads to a reference (see ``AlignParams``)."""
    from wgsextract_cli.commands.align import run_align

    return execute(params, run_align, progress=progress, cancel=cancel, check=check)


def realign(
    params: RealignParams,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
) -> CommandResult:
    """Re-align a BAM/CRAM to a reference (see ``RealignParams``)."""
    from wgsextract_cli.commands.realign import run_realign

    return execute(params, run_realign, progress=progress, cancel=cancel, check=check)
