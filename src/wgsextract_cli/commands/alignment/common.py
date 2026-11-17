"""Alignment common helpers."""

from __future__ import annotations

import logging
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, TypeVar

from wgsextract_cli.api._params import job_values
from wgsextract_cli.core.alignment.profiles import (
    output_profile_options,
)
from wgsextract_cli.core.chunked_alignment import Chunk
from wgsextract_cli.core.config import typed_settings
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.genome_library import pair_stem
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    run_command,
)

from .reads import (
    reads_stem,
)

if TYPE_CHECKING:
    from wgsextract_cli.api._params import Params

# BWA's default batch size depends on threads and changes pairing statistics.
BWA_BATCH_BASES = "100000000"


class InputReads(NamedTuple):
    """What an earlier stage measured of the reads: their number and mean
    length (each None when unknown)."""

    reads: int | None
    read_length: float | None = None


_Job = TypeVar("_Job", bound="AlignJob")


@dataclass(kw_only=True)
class AlignJob:
    """One alignment: its settings, with paths as strings, and what earlier
    stages learnt about its reads. ``align`` builds one from ``AlignParams``;
    realign one from ``RealignParams``, pointing ``r1``/``r2`` at the reads it
    extracted or streams."""

    r1: str | None = None
    r2: str | None = None
    input: str | None = None
    ref: str | None = None
    input_ref: str | None = None
    outdir: str | None = None
    threads: int | None = None
    memory: str | None = None
    memory_explicit: bool = False
    platform: str | None = None
    aligner: str = "auto"
    preset: str | None = None
    gpu: bool = False
    no_gpu_memory_check: bool = False
    sample: str | None = None
    long_read: bool = False
    format: str = "BAM"
    output_profile: str | None = None
    # bwa-mem3's --fast speed preset, whose alignments differ from BWA's.
    fast: bool = False
    # auto or off: WGS Extract's Apple Silicon aligner builds (native_aligners).
    native_aligners: str | None = None
    prefer_native_gpu: bool | None = None
    # Whether wgse-bwa reads the reference's dense suffix-array file (opt-in).
    dense_suffix_array: bool | None = None
    # samtools options decoding a CRAM r1 (realign's, or _cram_reference's).
    input_cram_opt: list[str] | None = None
    # Set once measured, so later stages don't read the reads again.
    input_reads: InputReads | None = None
    # The source BAM/CRAM's read-group DS, when realign aligns its FASTQ.
    read_group_description: str = ""
    # realign's chunks of a sorted long-read r1 (chunked_alignment), and
    # whether an interrupted chunked run's work may be resumed.
    chunks: list[Chunk] | None = None
    resume_chunks: bool = False
    # What the chunks read: r1, or a remote r1 with its local index.
    chunk_input: str | None = None
    exact_reads: int | None = None
    read_cleanup: ExitStack | None = None
    retire_fastqs: tuple[str, ...] = ()

    @classmethod
    def of(cls: type[_Job], params: Params) -> _Job:
        """The job of ``params``' fields that it shares."""
        job = cls(
            **job_values(params, cls), memory_explicit=params.explicitly_set("memory")
        )
        if params.COMMAND not in ("align", "realign"):
            return job
        job.output_profile = job.output_profile or typed_settings().output_profile
        output_profile_options(job.output_profile)
        job.format = job.format or "BAM"
        if job.output_profile != "default":
            if getattr(params, "format", None) == "BAM":
                if params.explicitly_set("output_profile"):
                    raise UsageError(
                        f"--output-profile {job.output_profile} writes CRAM, not "
                        "explicit --format BAM. Use --output-profile default "
                        "for BAM, or --format CRAM."
                    )
                logging.info(
                    "Using explicit --format BAM instead of config output_profile=%s.",
                    job.output_profile,
                )
                job.output_profile = "default"
            else:
                job.format = "CRAM"
        current_result().add_metrics(output_profile=job.output_profile)
        return job

    @property
    def read_count(self) -> int | None:
        return self.input_reads.reads if self.input_reads else None


class StreamingMemoryUnavailable(WGSExtractError):
    """Retry file extraction before starting an overlapping alignment pipeline."""


def aligned_output_path(
    outdir: str, r1: str, r2: str | None, fmt: str, aligner: str | None = None
) -> str:
    """Where alignment of r1 (and r2) writes: <stem>_aligned.bam or .cram
    (pbmm2 always writes BAM)."""
    stem = reads_stem(r1)
    if r2:
        stem = pair_stem(stem)
    ext = ".cram" if fmt == "CRAM" and aligner != "pbmm2" else ".bam"
    return str(absolute_path(Path(outdir, f"{stem}_aligned{ext}")))


class _AlignSetup(NamedTuple):
    threads: str
    memory: str
    outdir: str
    ref: str
    stem: str
    r1: str
    alignment_threads: str | None = None
    compression_threads: str | None = None


def ensure_bwa_index(ref: str) -> None:
    from wgsextract_cli.core.bwa_index import ensure_index

    try:
        ensure_index(ref, runner=run_command)
    except (OSError, subprocess.SubprocessError) as error:
        raise WGSExtractError(f"Automatic BWA indexing failed: {error}") from error


def ensure_fasta_index(ref: str) -> None:
    if not Path(ref + ".fai").is_file():
        run_command(["samtools", "faidx", ref])


def alt_contigs(ref: str) -> list[str]:
    """The reference's ALT haplotype contigs (GRCh38's *_alt, and the HLA alleles
    in hs38DH), which repeat stretches of the primary chromosomes."""
    fai = Path(ref + ".fai")
    ensure_fasta_index(ref)
    if not fai.is_file():
        return []
    names = [line.split("\t", 1)[0] for line in fai.read_text().splitlines()]
    return [n for n in names if n.endswith("_alt") or n.startswith("HLA-")]


def ensure_bwa_alt_file(ref: str) -> None:
    """Make BWA ALT-aware on a reference with ALT contigs. Without <ref>.alt,
    a read from a region that has an ALT copy aligns equally well to both and
    gets mapping quality 0, which variant callers count as no evidence: such
    regions lose their variants. BWA reads only the contig names from the file
    (an existing bwakit .alt, which also has their alignments, is kept)."""
    alt_file = Path(ref + ".alt")
    if alt_file.exists():
        return
    names = alt_contigs(ref)
    if not names:
        return
    try:
        alt_file.write_text("".join(f"{n}\n" for n in names), newline="\n")
    except OSError as e:
        raise WGSExtractError(
            f"{ref} has {len(names)} ALT contigs, and BWA needs {alt_file} listing "
            f"them to give reads in those regions a mapping quality: {e}"
        ) from e
    logging.info(f"Wrote {alt_file}: BWA maps ALT-aware over {len(names)} contigs.")


def duplicate_marker(paired: bool) -> str:
    """How short reads get their duplicates marked: samblaster in the stream
    when it's installed (it aborts on unpaired input), else samtools markdup,
    which samtools always provides."""
    if paired and get_tool_path("samblaster") is not None:
        return "samblaster"
    return "markdup"
