"""Conservative, build-aware sex evidence from distributed non-PAR windows.

These screening thresholds are not validated diagnostic accuracy estimates.
Targeted assays and atypical dosage should remain Unknown.
"""

from wgsextract_cli.core.utils import run_command


def nonpar_interval(chrom: str, length: int) -> tuple[int, int] | None:
    """Central non-PAR interval, in inclusive GRCh37/38 reference coordinates."""
    intervals = {
        ("X", 155270560): (2699521, 154931043),
        ("Y", 59373566): (2649521, 59034049),
        ("X", 156040895): (2781480, 155701382),
        ("Y", 57227415): (2781480, 56887902),
    }
    return intervals.get((chrom, length))


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

    Only GRCh37/38 sex chromosomes are supported. Unknown assemblies cannot
    supply trustworthy PAR boundaries and return no evidence.
    """
    if any(
        chrom not in contigs or nonpar_interval(chrom, contigs[chrom][1]) is None
        for chrom in ("X", "Y")
    ):
        return {}
    if (contigs["X"][1], contigs["Y"][1]) not in {
        (155270560, 59373566),
        (156040895, 57227415),
    }:
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
