"""Distributed synthetic reads on unequal, human-sized chromosome headers."""

from pathlib import Path

from tests.htslib_helpers import sam_to_bam


def sex_bam(
    path: Path,
    *,
    x_depth: int = 2,
    y_depth: int = 2,
    auto_depth: int = 4,
    build: str = "GRCh38",
    narrow_x: bool = False,
    par_only: bool = False,
    extra_flags: int = 0,
) -> str:
    lengths = (
        {"7": 159345973, "19": 58617616, "X": 155270560, "Y": 59373566}
        if build == "GRCh37"
        else {"7": 159345973, "19": 58617616, "X": 156040895, "Y": 57227415}
    )
    # Independently specified interior and boundary locations, in SAM coordinates.
    intervals = (
        {"X": (2699521, 154931043), "Y": (2649521, 59034049)}
        if build == "GRCh37"
        else {"X": (2781480, 155701382), "Y": (2781480, 56887902)}
    )
    rows = [f"@SQ\tSN:chr{c}\tLN:{n}" for c, n in lengths.items()]
    rows.append("@RG\tID:r\tSM:S")
    read = "A" * 100
    for chrom, depth in (
        ("7", auto_depth),
        ("19", auto_depth),
        ("X", x_depth),
        ("Y", y_depth),
    ):
        lo, hi = intervals.get(chrom, (1, lengths[chrom]))
        starts = [lo + (hi - lo + 1 - 10_000) * i // 10 for i in (1, 3, 5, 7, 9)]
        if par_only and chrom in {"X", "Y"}:
            starts = [lo - 10_000, hi + 1]
        for window in starts:
            for offset in range(0, 100 if narrow_x and chrom == "X" else 10_000, 100):
                for copy in range(depth):
                    rows.append(
                        f"A00123:8:HABCDEFXX:2:{chrom}:{window}:{offset + copy}\t{extra_flags}\tchr{chrom}"
                        f"\t{window + offset}\t60\t100M\t*\t0\t0\t{read}\t{'I' * 100}"
                        "\tRG:Z:r"
                    )
    return sam_to_bam(rows, path)
