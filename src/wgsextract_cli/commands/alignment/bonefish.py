"""Experimental CPU alignment for ONT and PacBio HiFi DNA."""

import re
import subprocess
from pathlib import Path

from wgsextract_cli.core.certification import CertificationLevel
from wgsextract_cli.core.dependency_checks import get_tool_version, verify_dependencies
from wgsextract_cli.core.reference_resolver import read_fai_lengths
from wgsextract_cli.core.resource_policy import stage_thread_plan
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.tool_catalog import INSTALL_HINTS
from wgsextract_cli.core.utils import UsageError, WGSExtractError

from .common import AlignJob, ensure_fasta_index
from .output import record_alignment, run_streamed_alignment
from .preparation import _prepare_alignment
from .read_groups import read_group
from .reads import long_read_mapper_input
from .selection import _bonefish_preset

_INDEX_BASES = 8_000_000_000


def record_bonefish_certification() -> None:
    if current_result().certification == CertificationLevel.EXPERIMENTAL.value:
        return
    current_result().certification = CertificationLevel.EXPERIMENTAL.value
    warn(
        "bonefish_experimental",
        "bonefish is Experimental: whole-genome equivalence with minimap2 has not been established by WGSE.",
    )


def verify_bonefish() -> None:
    verify_dependencies(["bonefish", "samtools"])
    version = get_tool_version("bonefish") or ""
    triplet = re.fullmatch(r"bonefish (\d+)\.(\d+)\.(\d+)", version.strip())
    if not triplet or tuple(map(int, triplet.groups())) < (0, 5, 2):
        raise UsageError(
            f"bonefish 0.5.2 or later is required to preserve modification tags "
            f"on full-length records (found {version or 'unknown version'}). "
            + INSTALL_HINTS["bonefish"]
        )
    samtools = get_tool_version("samtools") or ""
    release = re.match(r"samtools (\d+)\.(\d+)", samtools)
    if not release or tuple(map(int, release.groups())) < (1, 13):
        raise UsageError(
            "bonefish requires samtools 1.13 or later to read tagged FASTQ. "
            f"Update the samtools on PATH (found {samtools or 'unknown version'})."
        )


def align_bonefish(job: AlignJob) -> None:
    verify_bonefish()
    setup = _prepare_alignment(job, ["samtools"], "bonefish alignment")
    ensure_fasta_index(setup.ref)
    bases = sum(read_fai_lengths(setup.ref).values())
    # The normalized FASTQ/CRAM stream cannot be replayed for another part.
    if not 0 < bases <= _INDEX_BASES:
        raise UsageError(
            "bonefish needs an indexed reference FASTA of at most 8 billion bases "
            "for single-pass input. Use --aligner minimap2 for larger references."
        )
    plan = stage_thread_plan(job.threads)
    plan.record()
    preset = _bonefish_preset(job)
    upstream, description = long_read_mapper_input(job, setup.r1)
    ext = ".cram" if job.format == "CRAM" else ".bam"
    output = str(Path(setup.outdir, f"{setup.stem}_aligned{ext}"))
    command = [
        "bonefish", "-a", "-x", preset, "-t", str(plan.alignment), "-K", "10M",
        "-I", str(_INDEX_BASES), "-Y",
        "-R", read_group(setup.stem, job.sample, job.platform, description),
        *(["-y"] if upstream else []), setup.ref, "-" if upstream else setup.r1,
    ]  # fmt: skip
    try:
        run_streamed_alignment(
            command,
            output,
            threads=setup.threads,
            memory=setup.sort_budget,
            fmt=job.format,
            ref=setup.ref,
            duplicates=None,
            upstream=list(upstream) or None,
            exact_reads=job.exact_reads,
            output_profile=job.output_profile or "default",
        )
    except (OSError, subprocess.SubprocessError, WGSExtractError) as error:
        raise WGSExtractError(f"bonefish alignment failed: {error}") from error
    record_alignment(output, "bonefish", setup.ref)
