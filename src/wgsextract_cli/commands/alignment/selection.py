"""Backend selection and preset compatibility for alignment commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.native_support import require_native_tool
from wgsextract_cli.core.utils import UsageError

from .reads import _needs_read_extraction

if TYPE_CHECKING:
    from ..align import AlignJob

PACBIO_PLATFORMS = {"pacbio", "hifi", "clr"}
_LONG_READ_PLATFORMS = PACBIO_PLATFORMS | {"ont", "nanopore"}
# pbmm2-style preset names accepted by minimap2 under their minimap2 names.
_MINIMAP2_PRESET_ALIASES = {"ccs": "map-hifi", "subread": "map-pb"}
_PBMM2_PRESETS = {
    "map-hifi": "CCS",
    "ccs": "CCS",
    "map-pb": "SUBREAD",
    "subread": "SUBREAD",
}
# BWA-MEM and its descendants; bwa-mem3 can write either's exact records.
BWA_ALIGNERS = frozenset({"bwa", "bwa-mem2", "bwa-mem3"})


def select_aligner(job: AlignJob) -> str:
    """Resolve --aligner auto and reject nonsensical aligner/platform/preset combos."""
    long_read = job.long_read or job.platform in _LONG_READ_PLATFORMS
    aligner: str = job.aligner
    if job.platform in {"ont", "nanopore"} and job.gpu:
        raise UsageError(
            "ONT --gpu alignment is not qualified to preserve MM/ML methylation "
            "tags. Align without --gpu to keep them with samtools/minimap2."
        )
    if job.fast and (aligner not in ("auto", "bwa-mem3") or long_read or job.preset):
        raise UsageError(
            "--fast is bwa-mem3's speed preset for short reads: use it with "
            "--aligner bwa-mem3 (or auto)."
        )
    if job.fast and job.platform == "ultima":
        raise UsageError(
            "--fast doesn't apply to Ultima reads, which align with BWA-MEM (bwa "
            "or bwa-mem2) as Ultima's and Broad's pipelines do: drop --fast."
        )
    if job.fast and job.gpu:
        raise UsageError(
            "--fast is bwa-mem3's speed preset, and --gpu aligns with "
            "Parabricks' own BWA-MEM: drop one of them."
        )
    if job.platform == "ultima":
        require_native_tool("gatk")
        if aligner not in ("auto", "bwa", "bwa-mem2") or long_read or job.preset:
            raise UsageError(
                "Ultima reads align with BWA-MEM (--aligner auto, bwa or bwa-mem2), "
                "as Ultima's and Broad's pipelines do."
            )
        if job.gpu:
            raise UsageError(
                "--gpu (Parabricks) doesn't keep Ultima's flow tags (tp, t0) or "
                "mark flow-based duplicates; align Ultima reads without --gpu."
            )
        if job.r2:
            raise UsageError("Ultima reads are single-end: drop --r2.")
        return "bwa" if aligner == "auto" else aligner
    if aligner == "auto" and job.fast:
        aligner = "bwa-mem3"
    if aligner == "auto":
        if (
            job.platform in PACBIO_PLATFORMS
            and job.output_profile in (None, "default")
            and not _needs_read_extraction(job.r1)
            and get_tool_path("pbmm2") is not None
        ):
            aligner = "pbmm2"
        elif long_read or job.preset:
            aligner = "minimap2"
        else:
            aligner = "bwa"

    if aligner in BWA_ALIGNERS:
        if long_read:
            raise UsageError(
                "BWA-MEM is a short-read aligner; use --aligner minimap2 "
                "(or pbmm2 for PacBio) for long reads."
            )
        if job.preset:
            raise UsageError("--preset applies to minimap2 and pbmm2, not BWA.")
        if aligner != "bwa" and job.gpu:
            raise UsageError(
                "--gpu aligns with Parabricks' own BWA-MEM; use --aligner bwa or auto."
            )
    elif aligner == "pbmm2":
        if job.output_profile not in (None, "default"):
            raise UsageError(
                f"pbmm2 cannot write --output-profile {job.output_profile}; "
                "use --aligner minimap2 for CRAM profiles."
            )
        if job.platform in {"ont", "nanopore"}:
            raise UsageError(
                "pbmm2 only aligns PacBio reads; use --aligner minimap2 for ONT."
            )
        if job.preset and job.preset not in _PBMM2_PRESETS:
            raise UsageError(
                "PacBio pbmm2 alignment supports --preset map-hifi, map-pb, ccs, "
                "or subread."
            )
    return aligner


def requested_aligner(job: AlignJob) -> str:
    """--aligner, with --fast's bwa-mem3 for auto (select_aligner refuses others)."""
    return "bwa-mem3" if job.fast else job.aligner


def short_read_tool(job: AlignJob) -> str:
    """The BWA-MEM aligner asked for: bwa-mem3, bwa-mem2, or bwa (also for
    auto, which short_read_aligner resolves)."""
    requested = requested_aligner(job)
    return requested if requested in BWA_ALIGNERS else "bwa"


def is_long_read(job: AlignJob) -> bool:
    return bool(job.long_read) or job.platform in _LONG_READ_PLATFORMS


def _minimap2_preset(job: AlignJob) -> str:
    if job.preset:
        preset: str = job.preset
        return _MINIMAP2_PRESET_ALIASES.get(preset, preset)
    if job.platform in {"hifi", "pacbio"}:
        return "map-hifi"
    if job.platform in {"ont", "nanopore"}:
        return "map-ont"
    if job.platform == "clr" or job.long_read:
        return "map-pb"
    return "sr"
