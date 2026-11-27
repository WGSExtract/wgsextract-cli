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

from wgsextract_cli.commands._messages.align import CLI_HELP
from wgsextract_cli.core.alignment.profiles import OUTPUT_PROFILES
from wgsextract_cli.core.messages import CLI_HELP as SHARED_CLI_HELP
from wgsextract_cli.core.native_aligners import SETTINGS as NATIVE_ALIGNER_SETTINGS
from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.utils import UsageError

from ._network import DownloadParams
from ._params import Params, param
from ._runtime import CancelToken, ProgressCallback, execute
from ._shared import (
    dry_run_param,
    force_steps_param,
    input_param,
    memory_param,
    no_gpu_memory_check_param,
    no_preflight_param,
    optional_input_param,
    outdir_param,
    ref_param,
    restart_param,
    threads_param,
)

PLATFORMS = ("illumina", "dnbseq", "ultima", "pacbio", "hifi", "clr", "ont", "nanopore")
ALIGNERS = ("auto", "bwa", "bwa-mem2", "bwa-mem3", "minimap2", "pbmm2", "bonefish")
PRESETS = ("sr", "map-pb", "map-hifi", "map-ont", "ccs", "subread")
REALIGN_STEPS = ("extract", "align", "index")


def _native_aligners_param() -> Any:
    return param(
        None,
        help="Use installed native aligners (auto) or upstream tools (off). "
        "See native/README.md for platform support and setup.",
        doc="auto (the default, or config native_aligners) uses WGS Extract's "
        "aligner builds (Apple Silicon; wgse-bwa on Linux or Windows x86-64; "
        "wgse-bwa-mem3 on Linux x86-64; wgse-minimap2 on Linux and Windows x86-64) "
        "when installed on PATH "
        "(or wgse_bwa_path on Windows), and on Linux without wgse-bwa-mem3 "
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


def _prefer_native_gpu_param() -> Any:
    return param(
        None,
        help="Prefer native GPU alignment. Windows CUDA is automatic when it fits; "
        "--no-prefer-native-gpu forces CPU there.",
        doc="Windows CUDA is automatic within RAM and commit limits; false forces CPU. "
        "Elsewhere, true can override RAM limits. See the configuration guide.",
        action="boolean_optional",
        config_key="prefer_native_gpu",
    )


GPU_HELP = (
    "NVIDIA/Parabricks (Linux, Docker): BWA-MEM short reads, minimap2 HiFi; "
    "docs/hardware.md#nvidia-gpus-parabricks. ONT requires CPU "
    "alignment to retain MM/ML methylation tags."
)
_FORMAT_DOC = "Default: BAM. CRAM is written with the target reference."


def _output_profile_param() -> Any:
    return param(
        None,
        help="Lossless output: default keeps --format; archive selects CRAM 3.1 "
        "(GATK >=4.7); calling selects CRAM 3.0. Default: config output_profile, "
        "else default. See docs/ont.md.",
        choices=OUTPUT_PROFILES,
        config_key="output_profile",
    )


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
        help=SHARED_CLI_HELP["arg_r1"],
        doc=(
            "Read 1 FASTQ (or FASTA of long reads), or a BAM/CRAM of long reads "
            "(PacBio is detected) or of Ultima reads. Short reads in a BAM/CRAM "
            "go through realign."
        ),
        path="file",
    )
    r2: Path | None = param(
        None,
        help=SHARED_CLI_HELP["arg_r2"],
        doc="Read 2 FASTQ, for pairs.",
        path="file",
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
        "from bwa-mem2's index. Experimental bonefish 0.5.2+ maps ONT/HiFi DNA.",
        choices=ALIGNERS,
    )
    fast: bool = _fast_param()
    native_aligners: str | None = _native_aligners_param()
    prefer_native_gpu: bool | None = _prefer_native_gpu_param()
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
        "pbmm2, minimap2, or an explicitly selected bonefish.",
        action="store_true",
    )
    format: str | None = param(
        None,
        help="Output format (default: BAM)",
        doc=_FORMAT_DOC,
        choices=("BAM", "CRAM"),
    )
    output_profile: str | None = _output_profile_param()
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
    record with its MM/ML tags into minimap2 or experimental bonefish. Free disk
    space is checked first. Completed steps (extract, align, index) are
    recorded in ``<output>.steps.json`` and skipped on a rerun whose inputs,
    settings and tools are unchanged."""

    COMMAND = "realign"

    r1: Path | None = param(
        None,
        help=SHARED_CLI_HELP["arg_r1"],
        doc="Read 1 FASTQ already extracted from the input, to align instead "
        "of extracting it.",
        path="file",
    )
    r2: Path | None = param(
        None, help=SHARED_CLI_HELP["arg_r2"], doc="Read 2 FASTQ, with r1.", path="file"
    )
    aligner: str = param(
        "auto",
        help="Aligner to use (matches the 'align' command).",
        doc="Aligner: auto, bwa, bwa-mem2, bwa-mem3, minimap2, pbmm2 or "
        "experimental bonefish, chosen as for align.",
        choices=ALIGNERS,
    )
    fast: bool = _fast_param()
    native_aligners: str | None = _native_aligners_param()
    prefer_native_gpu: bool | None = _prefer_native_gpu_param()
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
        help="FASTA decoding the input CRAM, not the target --ref. Default: "
        "--ref if sequence names, lengths and MD5s match, else the matching "
        "reference-library FASTA.",
        path="file",
    )
    sample: str | None = param(
        None, help="Sample name to embed in read-group metadata."
    )
    long_read: bool = param(
        False, help="Treat input as long-read data (PacBio/ONT).", action="store_true"
    )
    format: str | None = param(
        None,
        help="Output format (default: BAM)",
        doc=_FORMAT_DOC,
        choices=("BAM", "CRAM"),
    )
    output_profile: str | None = _output_profile_param()
    keep_fastq: bool = param(
        False,
        help="Keep extracted FASTQs (normally deleted): pairs, or one tagged "
        "<input stem>.fastq.gz for ONT/PacBio. Supplied or reused FASTQs are "
        "never deleted.",
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
        help="ONT/PacBio input per chunk (K/M/G/T; 0: one pass). Resumes "
        "unfinished chunks and bounds temporary files. Requires sorted, "
        "indexed input and minimap2; ignored with --keep-fastq or bonefish.",
    )
    no_preflight: bool = no_preflight_param()
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
class BasecallParams(DownloadParams):
    """Basecall Nanopore POD5 signal with ONT's Dorado into an unaligned BAM,
    ``<input stem>.<model>[.<modifications>].bam``, that keeps the MM/ML
    base-modification tags and feeds ``align`` (``r1``) directly. Dorado is
    an optional tool the user installs; ``download`` permits model selection
    and downloads into ``models_dir`` (including chemistry/model identifiers
    sent to Oxford Nanopore). Without it, use a local model directory."""

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
