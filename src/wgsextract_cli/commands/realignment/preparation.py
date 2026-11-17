"""Realignment job, FASTQ discovery and input qualification."""

import logging
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.alignment_metadata import cram_encoded_with, get_bam_header
from wgsextract_cli.core.chunked_alignment import (
    parse_size,
)
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.read_counts import (
    unpaired_sample,
)
from wgsextract_cli.core.reference_assets import (
    find_fasta_by_signature,
    input_stem,
    reference_genomes_dir,
    resolve_reference_library,
)
from wgsextract_cli.core.sequencing import (
    pacbio_platform,
)
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
)
from wgsextract_cli.core.variant_files import (
    calculate_bam_md5,
    resolve_reference,
    verify_paths_exist,
)

from .. import align as align_cmd

# Patterns used to locate previously extracted FASTQ files next to a BAM/CRAM.
# Each entry is (suffix_for_r1, suffix_for_r2). Order is preference.
_FASTQ_PATTERNS: list[tuple[str, str]] = [
    ("_R1.fastq.gz", "_R2.fastq.gz"),
    ("_R1.fq.gz", "_R2.fq.gz"),
    ("_r1.fastq.gz", "_r2.fastq.gz"),
    ("_r1.fq.gz", "_r2.fq.gz"),
    (".R1.fastq.gz", ".R2.fastq.gz"),
    (".r1.fq.gz", ".r2.fq.gz"),
    ("_1.fastq.gz", "_2.fastq.gz"),
    ("_1.fq.gz", "_2.fq.gz"),
]


@dataclass(kw_only=True)
class RealignJob(align_cmd.AlignJob):
    """A realignment: the alignment's settings, the BAM/CRAM whose reads it
    realigns, and how those reach the aligner."""

    input: str
    keep_fastq: bool = False
    force_extract: bool = False
    no_preflight: bool = False
    chunk_size: str = "4G"


def _find_existing_fastqs(input_path: str, outdir: str) -> tuple[str, str] | None:
    """Look beside ``input_path`` and in ``outdir`` for an extracted R1/R2 pair."""
    base = input_stem(input_path)
    bam_dir = str(absolute_path(input_path).parent)
    for directory in dict.fromkeys([bam_dir, str(absolute_path(outdir))]):
        for r1_suffix, r2_suffix in _FASTQ_PATTERNS:
            r1 = str(Path(directory, base + r1_suffix))
            r2 = str(Path(directory, base + r2_suffix))
            if Path(r1).is_file() and Path(r2).is_file():
                return r1, r2
    return None


def _long_read_reference(job: RealignJob, target: str | None) -> str | None:
    """The FASTA a long-read alignment maps to: ``target``, else the one
    align resolves from --ref or the input's sequence dictionary."""
    if target and Path(target).is_file():
        return target
    resolved = resolve_reference(job.ref, calculate_bam_md5(job.input, None))
    return resolved if resolved and Path(resolved).is_file() else None


def _input_cram_reference(job: RealignJob) -> list[str]:
    """samtools -T option for decoding a CRAM input; empty for BAM."""
    if not job.input.lower().endswith(".cram"):
        return []
    if job.input_ref:
        verify_paths_exist({"--input-ref": job.input_ref})
        return ["-T", job.input_ref]
    reflib = resolve_reference_library()
    signature = calculate_bam_md5(job.input)
    target = job.ref
    # Re-mapping to the same build (e.g. with a newer aligner) decodes with --ref.
    if target and Path(target).is_file() and cram_encoded_with(target, job.input):
        fasta: str | None = target
    else:
        fasta = find_fasta_by_signature(
            [reflib, reference_genomes_dir(reflib)], signature
        )
    if not fasta:
        raise WGSExtractError(
            f"Neither --ref nor any FASTA in the reference library ({reflib}) "
            "matches the sequence dictionary of the input CRAM. Pass the "
            "reference it was encoded with via --input-ref."
        )
    logging.info("Decoding input CRAM with %s", fasta)
    return ["-T", fasta]


def _refuse_unpaired_reads(path: str, cram_opt: list[str]) -> None:
    """Paired extraction drops single-end reads; refuse rather than lose them."""
    sample = unpaired_sample(path, cram_opt=cram_opt)
    if sample.unpaired:
        raise WGSExtractError(
            f"{sample.unpaired:,} of {sample.records:,} sampled primary reads in "
            f"{path} (e.g. {sample.example}) are single-end: flagged neither first "
            "nor second of a pair. realign extracts paired reads and would drop "
            "them. Extract all reads with 'wgsextract bam unalign --r1 R1.fastq.gz "
            "--r2 R2.fastq.gz --se SE.fastq.gz' and align SE.fastq.gz (and any "
            "pairs) with 'wgsextract align', or pass --platform for ONT, PacBio "
            "or Ultima reads."
        )


def _single_end_pacbio(input_path: str, cram_opt: list[str]) -> str | None:
    """ "pacbio" (or "clr" for subreads) when every sampled primary read of a
    PacBio input is single-end, as PacBio reads are: realign then takes the
    long-read path instead of refusing them. Paired PacBio-labelled input keeps
    the short-read defaults."""
    sample = unpaired_sample(input_path, cram_opt=cram_opt)
    if not sample.records or sample.unpaired != sample.records:
        return None
    platform = pacbio_platform(
        get_bam_header(input_path, cram_opt[1] if cram_opt else None)
    )
    logging.info(
        "Detected single-end PacBio reads: realigning them as long reads "
        "(--platform %s).",
        platform,
    )
    return platform


def _check_gpu(job: RealignJob) -> None:
    """Refuse a GPU setup or GPU memory that can't run the alignment before
    the FASTQs are extracted; alignment plans its options when it starts."""
    from wgsextract_cli.core.parabricks import plan_gpu_memory, verify_parabricks

    verify_parabricks()
    plan_gpu_memory(
        "minimap2" if align_cmd.is_long_read(job) else "fq2bam",
        "realign",
        check=not job.no_gpu_memory_check,
        report=False,
    )


def _check_long_read_options(job: RealignJob) -> None:
    """realign streams long reads from the BAM/CRAM into minimap2 (not
    Parabricks; wgse-minimap2, on its NVIDIA GPU where it can)."""
    if job.aligner == "pbmm2":
        if job.platform not in align_cmd.PACBIO_PLATFORMS:
            raise UsageError(
                "pbmm2 aligns PacBio reads only; realign maps ONT reads with "
                "minimap2 (--aligner minimap2 or auto)."
            )
        raise UsageError(
            "realign aligns PacBio reads with minimap2, streaming each read's "
            "primary record with its MM/ML tags. pbmm2 reads a PacBio BAM "
            "itself, primary records only: run 'wgsextract align --r1 "
            "<BAM> --aligner pbmm2' (convert a CRAM with 'wgsextract bam "
            "to-bam' first)."
        )
    if job.gpu:
        raise UsageError(
            "realign aligns long reads with minimap2, not Parabricks, keeping "
            "their MM/ML tags; drop --gpu."
        )
    parse_size(job.chunk_size)
    if job.aligner == "auto":
        job.aligner = "minimap2"
