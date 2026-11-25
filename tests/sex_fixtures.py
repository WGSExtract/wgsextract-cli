"""Distributed synthetic reads on unequal, human-sized chromosome headers."""

from pathlib import Path

from tests.htslib_helpers import sam_to_bam
from wgsextract_cli.core.utils import run_command

_LENGTHS = {
    "GRCh37": {"7": 159345973, "19": 58617616, "X": 155270560, "Y": 59373566},
    "GRCh38": {"7": 159345973, "19": 58617616, "X": 156040895, "Y": 57227415},
}
# Independently specified non-PAR (Y: before Yq12) locations, in SAM coordinates.
_NONPAR = {
    "GRCh37": {"X": (2699521, 154931043), "Y": (2649521, 28819361)},
    "GRCh38": {"X": (2781480, 155701382), "Y": (2781480, 26673214)},
}


def _read(chrom: str, pos: int, name: str, flags: int = 0) -> str:
    return (
        f"A00123:8:HABCDEFXX:2:{chrom}:{name}\t{flags}\tchr{chrom}"
        f"\t{pos}\t60\t100M\t*\t0\t0\t{'A' * 100}\t{'I' * 100}\tRG:Z:r"
    )


def _header(build: str) -> list[str]:
    rows = [f"@SQ\tSN:chr{c}\tLN:{n}" for c, n in _LENGTHS[build].items()]
    return [*rows, "@RG\tID:r\tSM:S"]


def distributed_alignment(
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
    lengths, intervals = _LENGTHS[build], _NONPAR[build]
    rows = _header(build)
    for chrom, depth in (
        ("7", auto_depth),
        ("19", auto_depth),
        ("X", x_depth),
        ("Y", y_depth),
    ):
        lo, hi = intervals.get(chrom, (1, lengths[chrom]))
        starts = [lo + (hi - lo + 1 - 10_000) * i // 10 for i in (1, 3, 5, 7, 9)]
        if par_only and chrom in {"X", "Y"}:
            par2 = (
                (154931044 if chrom == "X" else 59034050)
                if build == "GRCh37"
                else (155701383 if chrom == "X" else 56887903)
            )
            starts = [lo - 10_000, par2]
        for window in starts:
            for offset in range(0, 100 if narrow_x and chrom == "X" else 10_000, 100):
                for copy in range(depth):
                    rows.append(
                        _read(
                            chrom,
                            window + offset,
                            f"{window}:{offset + copy}",
                            extra_flags,
                        )
                    )
    return sam_to_bam(rows, path)


def low_pass_alignment(
    path: Path,
    *,
    x_spacing: int,
    y_spacing: int,
    auto_spacing: int = 4_000,
    build: str = "GRCh38",
    cram: bool = False,
    capture_like: bool = False,
) -> str:
    """Whole-chromosome low-pass reads: one read every `*_spacing` bases (0 for
    none) along chr7, chr19 and non-PAR X/Y, about 0.025x at 4 kb.
    `capture_like` keeps reads only in alternate 500 kb blocks."""
    lengths, intervals = _LENGTHS[build], _NONPAR[build]
    rows = _header(build)
    for chrom, spacing in (
        ("7", auto_spacing),
        ("19", auto_spacing),
        ("X", x_spacing),
        ("Y", y_spacing),
    ):
        lo, hi = intervals.get(chrom, (1, lengths[chrom]))
        if spacing:
            rows += [
                _read(chrom, p, str(p))
                for p in range(lo, hi - 100, spacing)
                if not capture_like or p // 500_000 % 2 == 0
            ]
    bam = sam_to_bam(rows, path.with_suffix(".bam"))
    if not cram:
        return bam
    out = str(path.with_suffix(".cram"))
    # no_ref: the human-sized contigs have no FASTA to compress against.
    run_command(["samtools", "view", "-C", "-O", "cram,no_ref", "-o", out, bam])
    run_command(["samtools", "index", out])
    return out
