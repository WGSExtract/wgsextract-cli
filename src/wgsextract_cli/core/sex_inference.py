"""Conservative, build-aware sex evidence from distributed non-PAR windows.

These screening thresholds are not validated diagnostic accuracy estimates.
Targeted assays and atypical dosage should remain Unknown.
"""

import tempfile
from pathlib import Path

from wgsextract_cli.core.utils import run_command

# Half-open non-PAR intervals, keyed by the exact primary contig lengths.
_NONPAR = {
    (155270560, 59373566): ((2699520, 154931043), (2649520, 59034049)),
    (156040895, 57227415): ((2781479, 155701382), (2781479, 56887902)),
    (154259566, 62460029): ((2394410, 153925834), (2458320, 62122809)),
}
_YQ12_START = {59373566: 28819361, 57227415: 26673214}

# Low-pass sampling: enough reads to compare dosage well below 1x, still from
# indexed windows only, so a CRAM is never decoded whole.
LOW_PASS_WINDOWS = 100
LOW_PASS_WIDTH = 50_000


def nonpar_bounds(chrom: str, length: int) -> tuple[int, int] | None:
    """The zero-based, half-open interval between this contig's PARs."""
    if chrom in {"X", "Y"}:
        side = int(chrom == "Y")
        for lengths, intervals in _NONPAR.items():
            if lengths[side] == length:
                return intervals[side]
    return None


def nonpar_interval(chrom: str, length: int) -> tuple[int, int] | None:
    """One-based sampling interval, excluding the assembly's Yq12 N block."""
    bounds = nonpar_bounds(chrom, length)
    if bounds is None:
        return None
    start, end = bounds
    return start + 1, _YQ12_START.get(length, end) if chrom == "Y" else end


def coverage_windows(
    start: int, end: int, count: int = 5, width: int = 10_000
) -> list[tuple[int, int]]:
    """`count` evenly spread interior windows, avoiding telomeric gaps and
    non-PAR edges."""
    width = min(width, (end - start + 1) // count)
    return [
        (left, left + width - 1)
        for i in range(count)
        for left in [start + (end - start + 1 - width) * (2 * i + 1) // (2 * count)]
    ]


def _sampling_intervals(
    contigs: dict[str, tuple[str, int]],
) -> list[tuple[str, str, tuple[int, int]]]:
    """(chrom, contig name, one-based interval) for chr7, chr19 and non-PAR
    X/Y; nothing for assemblies without known PAR boundaries."""
    if (
        "X" not in contigs
        or "Y" not in contigs
        or (contigs["X"][1], contigs["Y"][1]) not in _NONPAR
    ):
        return []
    intervals: list[tuple[str, str, tuple[int, int]]] = []
    for chrom in ("7", "19", "X", "Y"):
        if chrom not in contigs:
            continue
        name, length = contigs[chrom]
        interval = (
            nonpar_interval(chrom, length) if chrom in {"X", "Y"} else (1, length)
        )
        if interval is not None and interval[1] - interval[0] >= 5:
            intervals.append((chrom, name, interval))
    return intervals


def sampled_depth(
    alignment: str, contigs: dict[str, tuple[str, int]], *, reference: str | None = None
) -> dict[str, tuple[float, float]]:
    """Mean depth and breadth in distributed windows, excluding low-quality,
    duplicate, secondary and supplementary alignments.

    Only GRCh37/38 and CHM13v2.0 sex chromosomes are supported. Unknown assemblies cannot
    supply trustworthy PAR boundaries and return no evidence.
    """
    evidence: dict[str, tuple[float, float]] = {}
    for chrom, name, interval in _sampling_intervals(contigs):
        depth = breadth = bases = 0.0
        for start, end in coverage_windows(*interval):
            result = run_command(
                [
                    "samtools",
                    "coverage",
                    "-H",
                    "-q",
                    "20",
                    "-Q",
                    "20",
                    "--ff",
                    "UNMAP,SECONDARY,QCFAIL,DUP,SUPPLEMENTARY",
                    "-r",
                    f"{name}:{start}-{end}",
                    *(["--reference", reference] if reference else []),
                    alignment,
                ],
                capture_output=True,
            )
            for line in result.stdout.splitlines():
                row = line.split("\t")
                if len(row) < 7:
                    continue
                width = int(row[2]) - int(row[1]) + 1
                bases += width
                breadth += float(row[5]) / 100 * width
                depth += float(row[6]) * width
        if bases:
            evidence[chrom] = (depth / bases, breadth / bases)
    return evidence


def _trimmed_mean(values: list[int]) -> float:
    """Mean without the top and bottom tenth: capture targets, repeats and
    assembly gaps make outlier windows."""
    ordered = sorted(values)
    cut = len(ordered) // 10
    kept = ordered[cut : len(ordered) - cut]
    return sum(kept) / len(kept)


def sampled_read_density(
    alignment: str, contigs: dict[str, tuple[str, int]], *, reference: str | None = None
) -> dict[str, tuple[float, float]]:
    """Reads per window (trimmed mean) and the fraction of windows holding any
    read, over many distributed 50 kb windows: for depths too low for
    `sampled_depth`'s five 10 kb windows.

    One indexed ``samtools bedcov`` pass reads every window, with
    `sampled_depth`'s read filters (MAPQ below 20, unmapped, secondary,
    supplementary, duplicate and QC-failed reads are excluded) and assemblies.
    """
    intervals = _sampling_intervals(contigs)
    if not intervals:
        return {}
    bed = "".join(
        f"{name}\t{start - 1}\t{end}\t{chrom}\n"
        for chrom, name, interval in intervals
        for start, end in coverage_windows(*interval, LOW_PASS_WINDOWS, LOW_PASS_WIDTH)
    )
    with tempfile.TemporaryDirectory(prefix="wgse_sex_") as folder:
        bed_path = Path(folder, "windows.bed")
        bed_path.write_text(bed, encoding="utf-8", newline="\n")
        result = run_command(
            [
                "samtools",
                "bedcov",
                "-c",
                "-Q",
                "20",
                # bedcov already drops UNMAP,SECONDARY,QCFAIL,DUP reads
                "-G",
                "SUPPLEMENTARY",
                *(["--reference", reference] if reference else []),
                str(bed_path),
                alignment,
            ],
            capture_output=True,
        )
    counts: dict[str, list[int]] = {}
    for line in result.stdout.splitlines():
        row = line.split("\t")
        if len(row) >= 6:
            counts.setdefault(row[3], []).append(int(row[-1]))
    return {
        chrom: (_trimmed_mean(reads), sum(n > 0 for n in reads) / len(reads))
        for chrom, reads in counts.items()
    }
