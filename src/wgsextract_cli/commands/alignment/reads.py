"""Read-source classification, validation and sampling for alignment."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from wgsextract_cli.core import ultima
from wgsextract_cli.core.alignment.metadata import cram_encoded_with, get_bam_header
from wgsextract_cli.core.alignment.read_counts import (
    alignment_sample,
    fasta_read_length,
    fastq_statistics,
)
from wgsextract_cli.core.alignment.sequencing import (
    input_platform,
    pacbio_platform,
    record_platform_choice,
)
from wgsextract_cli.core.ref_library.assets import header_sequences, is_fasta_path
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.core.variant_files import verify_paths_exist

if TYPE_CHECKING:
    from ..align import AlignJob

# Base-modification tags (and ONT's MN), and the parent read ID (pi) of a read
# Dorado split, that long reads keep through alignment.
LONG_READ_TAGS = "MM,ML,MN,pi"
_READ_FILE_SUFFIXES = (
    ".fastq.gz",
    ".fq.gz",
    ".fastq",
    ".fq",
    ".subreads.bam",
    ".ccs.bam",
    ".bam",
)


def resolve_platform(
    platform: str | None,
    reads: str | None,
    input_ref: str | None = None,
    r2: str | None = None,
) -> str:
    resolved = _resolve_platform(platform, reads, input_ref, r2)
    for path in (reads, r2):
        record_platform_choice(path, resolved)
    return resolved


def _resolve_platform(
    platform: str | None,
    reads: str | None,
    input_ref: str | None,
    r2: str | None,
) -> str:
    """Use long-read defaults for ONT and PacBio FASTQ evidence."""
    if platform:
        if reads and not _is_alignment_file(reads):
            input_platform(reads, input_ref, override=platform)
            if r2:
                input_platform(r2, input_ref, override=platform)
        return platform
    if r2:
        first = resolve_platform(None, reads, input_ref)
        second = resolve_platform(None, r2, input_ref)
        if first != second and {first, second} & {"ont", "pacbio"}:
            raise UsageError(
                "FASTQ mates have conflicting platform evidence. Pass "
                "--platform ont, --platform hifi or --platform clr only if "
                "both files contain that kind of reads, or split the inputs."
            )
        return first
    detected = input_platform(reads, input_ref, strict=True)
    if reads and reads.lower().endswith((".bam", ".cram")) and Path(reads).is_file():
        if ultima.is_ultima_header(get_bam_header(reads, input_ref)):
            logging.info(
                "%s has an Ultima read group: retaining Ultima defaults.", reads
            )
            return "ultima"
    if detected == "ont" or (
        detected == "pacbio" and reads and not _is_alignment_file(reads)
    ):
        logging.info(
            "Detected %s reads: selecting platform-specific alignment defaults.",
            detected,
        )
        return detected
    if detected and detected != "illumina":
        logging.info(
            "Detected non-Illumina reads; retaining existing Illumina alignment defaults. "
            "Use --platform to request different defaults explicitly."
        )
    return "illumina"


def _alignment_file_platform(job: AlignJob) -> str | None:
    """The platform of PacBio reads in a BAM/CRAM/SAM --r1 (such as the
    *.ccs.bam or *.subreads.bam that --genome picks): they align as long reads."""
    reads = job.r1
    if job.platform or not reads or not _is_alignment_file(reads):
        return None
    input_ref = job.input_ref
    if input_platform(reads, input_ref, strict=True) != "pacbio":
        return None
    platform = pacbio_platform(get_bam_header(reads, input_ref))
    logging.info(
        "Detected PacBio reads in %s: aligning them as long reads (--platform %s).",
        reads,
        platform,
    )
    record_platform_choice(reads, platform)
    return platform


def _is_alignment_file(path: object) -> bool:
    return str(path).lower().endswith((".bam", ".cram", ".sam"))


def _needs_read_extraction(path: object) -> bool:
    """CRAM and SAM reads, which pbmm2 and Parabricks can't read: only
    samtools fastq (in the minimap2 path) decodes them."""
    return str(path or "").lower().endswith((".cram", ".sam"))


def _refuse_unreadable_reads(job: AlignJob, aligner: str) -> None:
    reads = job.r1
    if not _needs_read_extraction(reads):
        return
    if job.gpu:
        raise UsageError(
            f"{reads} is a CRAM/SAM, and --gpu (Parabricks) reads FASTQ or a BAM. "
            "Align it without --gpu, or convert it to BAM first."
        )
    if aligner == "pbmm2":
        raise UsageError(
            f"{reads} is a CRAM/SAM, and pbmm2 reads BAM, FASTQ or FASTA. "
            "Use --aligner minimap2 (or auto)."
        )


def _cram_reference(job: AlignJob) -> None:
    """The FASTA a CRAM --r1 decodes with: --input-ref, else --ref when it
    encoded the CRAM. realign passes the one it found as input_cram_opt."""
    reads = str(job.r1 or "")
    if (
        not reads.lower().endswith(".cram")
        or not Path(reads).is_file()
        or job.input_cram_opt
    ):
        return
    fasta = job.input_ref
    if fasta:
        verify_paths_exist({"--input-ref": fasta})
    elif not header_sequences(get_bam_header(reads)):
        return  # Unaligned reads: the CRAM holds every base itself.
    elif job.ref and Path(job.ref).is_file() and cram_encoded_with(job.ref, reads):
        fasta = job.ref
    else:
        raise UsageError(
            f"{reads} is a CRAM, and --ref doesn't match the reference it was "
            "encoded with. Pass that FASTA with --input-ref, or realign the CRAM "
            f"with 'wgsextract realign --input {reads} --ref <reference>', which "
            "also finds it in the reference library."
        )
    job.input_ref = str(fasta)
    job.input_cram_opt = ["--reference", str(fasta)]


def _refuse_short_read_alignment_file(job: AlignJob) -> None:
    """Short-read aligners read FASTQ; pairing reads out of a BAM/CRAM is
    realign's job (#447)."""
    for reads in (job.r1, job.r2):
        if reads and _is_alignment_file(reads):
            raise UsageError(
                f"{reads} is a BAM/CRAM/SAM file, and short reads align from "
                f"FASTQ. Realign its reads with 'wgsextract realign --input "
                f"{reads} --ref <reference>', or pass --platform for long "
                "(ONT or PacBio) reads."
            )


def reads_stem(path: str) -> str:
    """File name of a FASTQ/unaligned-BAM read file without its read-file suffix."""
    name = Path(path).name
    for suffix in _READ_FILE_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def long_read_fastq_command(path: str, cram_opt: Sequence[str] = ()) -> list[str]:
    """samtools fastq writing each read of a BAM/CRAM once (its primary record,
    not secondary or supplementary copies) with its modification tags."""
    return [
        "samtools",
        "fastq",
        "-n",
        "-F",
        "0x900",
        "-T",
        LONG_READ_TAGS,
        *cram_opt,
        path,
    ]


def _long_read_length(reads: str, cram_opt: Sequence[str]) -> float | None:
    """Sampled mean read length, which scales minimap2's long-read buffers."""
    if _is_alignment_file(reads):
        sample = alignment_sample(reads, cram_opt=list(cram_opt), single_end=True)
        mean = sample.read_length
    elif is_fasta_path(reads):
        mean = fasta_read_length(reads)
    else:
        statistics = fastq_statistics(reads)
        mean = statistics.read_length if statistics is not None else None
    if mean is not None:
        logging.info("Sampled mean read length: %s bp.", f"{mean:,.0f}")
    return mean
