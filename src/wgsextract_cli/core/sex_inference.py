"""Conservative, build-aware sex evidence from distributed non-PAR windows.

These screening thresholds are not validated diagnostic accuracy estimates.
Targeted assays and atypical dosage should remain Unknown.
"""

from wgsextract_cli.core.utils import run_command

# Half-open non-PAR intervals, keyed by the exact primary contig lengths.
_NONPAR = {
    (155270560, 59373566): ((2699520, 154931043), (2649520, 59034049)),
    (156040895, 57227415): ((2781479, 155701382), (2781479, 56887902)),
    (154259566, 62460029): ((2394410, 153925834), (2458320, 62122809)),
}
_YQ12_START = {59373566: 28819361, 57227415: 26673214}


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


def coverage_windows(start: int, end: int) -> list[tuple[int, int]]:
    """Five interior 10 kb windows, avoiding telomeric gaps and non-PAR edges."""
    width = min(10_000, (end - start + 1) // 5)
    return [
        (left, left + width - 1)
        for i in range(5)
        for left in [start + (end - start + 1 - width) * (2 * i + 1) // 10]
    ]


def sampled_depth(
    alignment: str, contigs: dict[str, tuple[str, int]], *, reference: str | None = None
) -> dict[str, tuple[float, float]]:
    """Mean depth and breadth in distributed windows, excluding low-quality,
    duplicate, secondary and supplementary alignments.

    Only GRCh37/38 and CHM13v2.0 sex chromosomes are supported. Unknown assemblies cannot
    supply trustworthy PAR boundaries and return no evidence.
    """
    if (
        "X" not in contigs
        or "Y" not in contigs
        or (contigs["X"][1], contigs["Y"][1]) not in _NONPAR
    ):
        return {}
    evidence: dict[str, tuple[float, float]] = {}
    for chrom in ("7", "19", "X", "Y"):
        if chrom not in contigs:
            continue
        name, length = contigs[chrom]
        interval = (
            nonpar_interval(chrom, length) if chrom in {"X", "Y"} else (1, length)
        )
        if interval is None or interval[1] - interval[0] < 5:
            continue
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
