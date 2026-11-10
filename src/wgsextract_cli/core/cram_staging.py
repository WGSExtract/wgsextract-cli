"""Temporary CRAM 3.0 copies for tools whose own htslib can't read CRAM 3.1.

Old htscodecs (before 1.5.2, bundled with htslib before 1.19) has an SSE4 rANS
decoder bug that fails on valid CRAM 3.1 blocks. Tools that ship or statically
link such an htslib (DeepVariant's x86-64 images, Clair3's libclair3) get a
CRAM 3.0 copy instead; the host samtools decodes the original correctly.
"""

import gzip
import logging
import shutil
import zlib
from bisect import bisect_left
from pathlib import Path

from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.reference_assets import read_fai_lengths
from wgsextract_cli.core.region_selection import Intervals, selected_intervals
from wgsextract_cli.core.samtools_commands import (
    alignment_index,
    get_sam_index_cmd,
    get_sam_view_cmd,
)
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.warnings import GB, check_free_space, get_free_space_needed


def cram_version(path: str) -> tuple[int, int] | None:
    """Read the CRAM file definition, independently of the filename."""
    with open(path, "rb") as source:
        definition = source.read(6)
    if len(definition) != 6 or definition[:4] != b"CRAM":
        return None
    return definition[4], definition[5]


def needs_cram30(alignment: str) -> bool:
    """Whether ``alignment`` is CRAM 3.1 or newer."""
    version = cram_version(alignment)
    return version is not None and version >= (3, 1)


def region_intervals(region: str, lengths: dict[str, int], tool: str) -> Intervals:
    try:
        return selected_intervals(region, lengths)
    except (ValueError, OSError) as error:
        raise WGSExtractError(
            f"Invalid {tool} region selection: {region}: {error}"
        ) from error


def _reference_lengths(ref: str) -> dict[str, int]:
    lengths = read_fai_lengths(ref)
    if not lengths:
        run_command(["samtools", "faidx", ref])
        lengths = read_fai_lengths(ref)
    return lengths


def _transcode_regions(
    region: str | None, ref: str, work: str, tool: str
) -> tuple[list[str], Intervals]:
    if not region:
        return [], {}
    # Whitespace-separated literals and BED/BED.gz/BEDPE files.
    # One BED + the multi-region iterator preserves their union without duplicates.
    merged = region_intervals(region, _reference_lengths(ref), tool)
    bed = Path(work, "regions.bed")
    with bed.open("w", newline="\n") as destination:
        for chrom, ranges in merged.items():
            for start, end in ranges:
                destination.write(f"{chrom}\t{start}\t{end}\n")
    return ["-M", "-L", str(bed)], merged


def _indexed_slice_bytes(
    alignment: str, ref: str, intervals: dict[str, list[tuple[int, int]]]
) -> int | None:
    """CRAI slice sizes reflect capture depth; reference-base fractions do not."""
    index = alignment_index(alignment)
    if not index or not index.endswith(".crai"):
        return None
    try:
        names = [
            next(field[3:] for field in line.split("\t") if field.startswith("SN:"))
            for line in get_bam_header(alignment, ref).splitlines()
            if line.startswith("@SQ\t")
        ]
        if not names or not all(names) or len(set(names)) != len(names):
            return None
        with open(index, "rb") as signature:
            compressed = signature.read(2) == b"\x1f\x8b"
        opener = gzip.open if compressed else open
        seen: dict[tuple[int, int], int] = {}
        selected: set[tuple[int, int]] = set()
        with opener(index, "rt") as source:
            for line in source:
                rid, start, span, container, offset, size = map(int, line.split())
                if (
                    rid < -1
                    or rid >= len(names)
                    or min(container, offset) < 0
                    or size <= 0
                    or (rid >= 0 and (start < 1 or span < 1))
                ):
                    return None
                key = (container, offset)
                if key in seen and seen[key] != size:
                    return None
                seen[key] = size
                if rid == -1:
                    continue
                ranges = intervals.get(names[rid], [])
                start -= 1  # CRAI is 1-based; the staging BED is 0-based.
                hit = bisect_left(ranges, (start + span, 0)) - 1
                if hit >= 0 and ranges[hit][1] > start:
                    # A multi-reference slice has one CRAI row per reference.
                    selected.add(key)
        return sum(seen[key] for key in selected) if seen else None
    except (OSError, EOFError, ValueError, StopIteration, zlib.error):
        return None


def stage_cram30(
    alignment: str,
    ref: str,
    work: str,
    threads: str,
    region: str | None,
    *,
    tool: str,
    why: str,
) -> str:
    """``alignment``, or for CRAM 3.1+ an indexed CRAM 3.0 copy in ``work``
    (only the ``region`` selection, when given) that ``tool`` can read."""
    version = cram_version(alignment)
    if version is None or version < (3, 1):
        return alignment
    selection, intervals = _transcode_regions(region, ref, work, tool)
    _, final_gb = get_free_space_needed(Path(alignment).stat().st_size, is_cram=True)
    required_gb = final_gb + 1  # Round up the CRAM expansion estimate; allow its index.
    selected_bytes = _indexed_slice_bytes(alignment, ref, intervals) if region else None
    # Round the existing ~1.95x CRAM expansion allowance up to 2x for slices.
    required_bytes = (
        max(64 * 1024**2, 2 * selected_bytes)
        if selected_bytes is not None
        else required_gb * GB
    )
    enough_space = (
        shutil.disk_usage(work).free >= required_bytes
        if region
        else check_free_space(work, required_gb)
    )
    if not enough_space:
        raise WGSExtractError(
            f"Insufficient free disk space for {tool} CRAM 3.0 staging "
            f"at {work} (needs ~{required_bytes / GB:.2f} GiB)."
        )
    logging.info("Transcoding CRAM %s.%s to temporary CRAM 3.0: %s", *version, why)
    compatible = str(Path(work, "reads.cram"))
    run_command(
        [
            *get_sam_view_cmd(threads, fmt="CRAM", reference=ref),
            "--output-fmt-option", "version=3.0",
            "--no-PG", *selection, "-o", compatible, alignment,
        ]
    )  # fmt: skip
    run_command(get_sam_index_cmd(compatible, threads))
    return compatible
