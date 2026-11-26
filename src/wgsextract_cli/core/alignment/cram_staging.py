"""Temporary copies of CRAM input for tools that can't read it as it is.

Temporary CRAM 3.0 copies are for tools whose own htslib can't read CRAM 3.1.

Old htscodecs (before 1.5.2, bundled with htslib before 1.19) has an SSE4 rANS
decoder bug that fails on valid CRAM 3.1 blocks. Tools that ship or statically
link such an htslib (DeepVariant's x86-64 images, Clair3's libclair3 before 2.0.5) get a
CRAM 3.0 copy instead; the host samtools decodes the original correctly.
Tools that take no reference (pbsv) get a BAM copy, so their htslib never
looks the reference up by itself (#636). htsjdk tools (GATK) get a fresh
temporary index of a CRAM whose own .crai htsjdk refuses (#1034).
"""

import gzip
import logging
import shutil
import zlib
from bisect import bisect_left
from pathlib import Path

from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.alignment.samtools_commands import (
    alignment_index,
    get_sam_index_cmd,
)
from wgsextract_cli.core.paths import PathInput
from wgsextract_cli.core.ref_library.assets import read_fai_lengths
from wgsextract_cli.core.region_selection import Intervals, selected_intervals
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


def _htsjdk_crai(alignment: str) -> Path | None:
    """The .crai htsjdk opens for a CRAM: x.crai or x.cram.crai (before
    x.cram.bai or .csi) beside it, else beside the file a link points to."""
    for cram in (Path(alignment), Path(alignment).resolve()):
        for index in (
            *([cram.with_suffix(".crai")] if cram.suffix == ".cram" else []),
            *(Path(f"{cram}{ext}") for ext in (".crai", ".bai", ".csi")),
        ):
            if index.is_file():
                return index if index.suffix == ".crai" else None
    return None


def _htsjdk_crai_problem(
    index: PathInput, *, require_valid_rows: bool = False
) -> str | None:
    """Why htsjdk (GATK, Picard) would refuse this .crai, or None.

    htsjdk converts a .crai to a BAI as it opens the CRAM and refuses an
    unmapped-slice row (reference -1) unless its start is 0 or -1 and its span
    0 or 1. Older CRAM writers gave those rows the read length as their span,
    as in the 1000 Genomes CRAMs' indexes (#1034); htslib reads them."""
    try:
        with open(index, "rb") as signature:
            compressed = signature.read(2) == b"\x1f\x8b"
        if require_valid_rows and not compressed:
            return "the index is not gzip-compressed"
        opener = gzip.open if compressed else open
        with opener(index, "rt") as source:
            number = 0
            for number, line in enumerate(source, 1):
                fields = (
                    line.rstrip("\r\n").split("\t")
                    if require_valid_rows
                    else line.split()
                )
                if len(fields) != 6:
                    return (
                        f"row {number} does not have six fields"
                        if require_valid_rows
                        else None
                    )
                rid, start, span = map(int, fields[:3])
                if require_valid_rows:
                    values = tuple(map(int, fields))
                    i32, i64 = 2**31 - 1, 2**63 - 1
                    bounds = (
                        (-1, i32),
                        (-1, i32),
                        (0, i32),
                        (0, i64),
                        (0, i32),
                        (0, i32),
                    )
                    if any(
                        not field.isascii() or not field.removeprefix("-").isdigit()
                        for field in fields
                    ) or any(
                        not low <= value <= high
                        for value, (low, high) in zip(values, bounds, strict=True)
                    ):
                        return f"row {number} has an invalid CRAI field"
                if rid == -1 and (start not in (0, -1) or span not in (0, 1)):
                    return (
                        f"row {number} is an unmapped slice with start {start} "
                        f"and span {span}"
                    )
            if require_valid_rows and not number:
                return "the index has no rows"
    except (OSError, EOFError, ValueError, zlib.error) as error:
        return str(error) if require_valid_rows else None
    return None


def usable_htsjdk_crai(alignment: str) -> str | None:
    """An explicit, structurally readable CRAI for a qualified direct reader."""
    index = _htsjdk_crai(alignment)
    canonical = Path(f"{alignment}.crai")
    # htslib opens x.cram.crai first; htsjdk opens x.crai first.
    if (
        index is None
        or not canonical.is_file()
        or canonical.resolve() != index.resolve()
        or _htsjdk_crai_problem(index, require_valid_rows=True) is not None
    ):
        return None
    return str(index)


def htsjdk_cram_index(
    alignment: str, work: str, threads: str, *, tool: str
) -> str | None:
    """A fresh index in ``work`` for a CRAM whose own .crai htsjdk refuses
    (see _htsjdk_crai_problem), to pass to ``tool`` explicitly; otherwise None.
    The CRAM and its index are left as they are."""
    if cram_version(alignment) is None:
        return None
    index = _htsjdk_crai(alignment)
    problem = _htsjdk_crai_problem(index) if index else None
    if index is None or problem is None:
        return None
    # A fresh .crai is about the size of the old one.
    required = 2 * index.stat().st_size + 1024**2
    if shutil.disk_usage(work).free < required:
        raise WGSExtractError(
            f"Insufficient free disk space for a temporary CRAM index at {work} "
            f"(needs ~{required / 1024**2:.0f} MiB)."
        )
    logging.info(
        "%s can't read the CRAM index %s (%s); indexing the CRAM again into a "
        "temporary index for it. The CRAM and its index are not changed.",
        tool, index, problem,
    )  # fmt: skip
    fresh = str(Path(work, "reads.cram.crai"))
    run_command(["samtools", "index", "-@", threads, "-o", fresh, alignment])
    return fresh


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
    region: str | Intervals | None, ref: str, work: str, tool: str
) -> tuple[list[str], Intervals]:
    if not region:
        return [], {}
    # Whitespace-separated literals and BED/BED.gz/BEDPE files, or intervals.
    # One BED + the multi-region iterator preserves their union without duplicates.
    merged = (
        region
        if isinstance(region, dict)
        else region_intervals(region, _reference_lengths(ref), tool)
    )
    bed = Path(work, "regions.bed")
    with bed.open("w", newline="\n") as destination:
        for chrom, ranges in merged.items():
            for start, end in ranges:
                destination.write(f"{chrom}\t{start}\t{end}\n")
    return ["-M", "-L", str(bed)], merged


def indexed_slice_bytes(
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


def staging_bytes(alignment: str, ref: str, intervals: Intervals | None) -> int:
    """The disk a staged copy of ``alignment`` (the ``intervals`` selected
    from it, when given) is allowed: the CRAM's ~1.95x expansion and its
    index, or twice its selected CRAI slices and at least 64 MiB."""
    selected = indexed_slice_bytes(alignment, ref, intervals) if intervals else None
    if selected is not None:
        return max(64 * 1024**2, 2 * selected)
    _, final_gb = get_free_space_needed(Path(alignment).stat().st_size, is_cram=True)
    # Round up the CRAM expansion estimate; allow its index.
    return (final_gb + 1) * GB


def stage_cram30(
    alignment: str,
    ref: str,
    work: str,
    threads: str,
    region: str | Intervals | None,
    *,
    tool: str,
    why: str,
) -> str:
    """``alignment``, or for CRAM 3.1+ an indexed CRAM 3.0 copy in ``work``
    (only the ``region`` selection, when given) that ``tool`` can read.

    ``region`` is DeepVariant-style text (whitespace-separated literals and
    BED files) or already merged, sorted, non-overlapping intervals."""
    version = cram_version(alignment)
    if version is None or version < (3, 1):
        return alignment
    logging.info("Transcoding CRAM %s.%s to temporary CRAM 3.0: %s", *version, why)
    return _stage(alignment, ref, work, threads, region, tool, "CRAM 3.0")


def stage_bam(
    alignment: str, ref: str, work: str, threads: str, region: str | None, *, tool: str
) -> str:
    """``alignment``, or for CRAM an indexed BAM copy in ``work`` (only the
    ``region`` selection, when given), for ``tool``, which takes no reference
    to decode CRAM with."""
    if cram_version(alignment) is None:
        return alignment
    logging.info("Decoding CRAM to a temporary BAM: %s takes no reference.", tool)
    return _stage(alignment, ref, work, threads, region, tool, "BAM")


def _stage(
    alignment: str,
    ref: str,
    work: str,
    threads: str,
    region: str | Intervals | None,
    tool: str,
    fmt: str,
) -> str:
    selection, intervals = _transcode_regions(region, ref, work, tool)
    required_bytes = staging_bytes(alignment, ref, intervals if region else None)
    enough_space = (
        shutil.disk_usage(work).free >= required_bytes
        if region
        else check_free_space(work, required_bytes // GB)
    )
    if not enough_space:
        raise WGSExtractError(
            f"Insufficient free disk space for {tool} {fmt} staging "
            f"at {work} (needs ~{required_bytes / GB:.2f} GiB)."
        )
    cram30 = fmt != "BAM"
    compatible = str(Path(work, "reads.cram" if cram30 else "reads.bam"))
    run_command(
        [
            "samtools", "view", "-@", threads, "-T", ref,
            *(["-O", "CRAM", "--output-fmt-option", "version=3.0"] if cram30 else ["-b"]),
            "--no-PG", *selection, "-o", compatible, alignment,
        ]
    )  # fmt: skip
    run_command(get_sam_index_cmd(compatible, threads))
    return compatible
