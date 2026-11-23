"""Transcript and exon models that ``ref gene-map`` installs.

``ref gene-map`` downloads UCSC's refGene table (NCBI RefSeq transcripts,
public domain) and, beside the gene map, keeps every transcript on a primary
contig as ``transcripts_<build>.bed.gz``: a BGZF BED12 with its tabix index,
named ``SYMBOL|ACCESSION`` (igv splits BED columns on any white space), whose
thickStart/thickEnd mark the CDS. The genome viewer draws it as a track and
``info gene`` reads one gene's transcripts from it by region; nothing is
fetched when it is read.
"""

from __future__ import annotations

import gzip
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.ref_library.layout import download_dirs
from wgsextract_cli.core.utils import WGSExtractError, popen, run_command

TRANSCRIPTS_NAME = "transcripts_{build}.bed.gz"


@dataclass(frozen=True)
class Transcript:
    symbol: str
    accession: str
    chrom: str
    strand: str
    start: int  # 0-based
    end: int  # exclusive
    cds: tuple[int, int] | None  # 0-based, half-open; None when non-coding
    exons: tuple[tuple[int, int], ...]  # 0-based, half-open, in genome order

    @property
    def cds_length(self) -> int:
        if not self.cds:
            return 0
        first, last = self.cds
        return sum(
            max(0, min(end, last) - max(start, first)) for start, end in self.exons
        )


def from_refgene(columns: Sequence[str]) -> Transcript | None:
    """A refGene row (bin, name, chrom, strand, txStart, txEnd, cdsStart,
    cdsEnd, exonCount, exonStarts, exonEnds, score, name2, ...), or None for
    a short row or one whose exons don't fit the transcript."""
    if len(columns) < 13:
        return None
    starts = [int(x) for x in columns[9].rstrip(",").split(",") if x]
    ends = [int(x) for x in columns[10].rstrip(",").split(",") if x]
    tx_start, tx_end = int(columns[4]), int(columns[5])
    exons = sorted(zip(starts, ends, strict=False))
    if (
        not exons
        or len(starts) != len(ends)
        or any(not tx_start <= a < b <= tx_end for a, b in exons)
    ):
        return None  # exons that don't fit the transcript: not a usable model
    cds_start, cds_end = int(columns[6]), int(columns[7])
    return Transcript(
        symbol=columns[12],
        accession=columns[1],
        chrom=columns[2],
        strand=columns[3],
        start=tx_start,
        end=tx_end,
        cds=(cds_start, cds_end) if cds_end > cds_start else None,
        exons=tuple(exons),
    )


def bed12(transcript: Transcript) -> str:
    t = transcript
    thick = t.cds or (t.start, t.start)
    sizes = ",".join(str(end - start) for start, end in t.exons)
    offsets = ",".join(str(start - t.start) for start, _ in t.exons)
    return (
        f"{t.chrom}\t{t.start}\t{t.end}\t{t.symbol}|{t.accession}\t0\t{t.strand}\t"
        f"{thick[0]}\t{thick[1]}\t0\t{len(t.exons)}\t{sizes},\t{offsets},\n"
    )


def from_bed12(line: str) -> Transcript:
    c = line.rstrip("\r\n").split("\t")
    if len(c) < 12:
        raise ValueError("not a BED12 row")
    start, end = int(c[1]), int(c[2])
    symbol, _, accession = c[3].partition("|")
    sizes = [int(x) for x in c[10].rstrip(",").split(",")]
    offsets = [int(x) for x in c[11].rstrip(",").split(",")]
    thick = (int(c[6]), int(c[7]))
    return Transcript(
        symbol=symbol,
        accession=accession,
        chrom=c[0],
        strand=c[5],
        start=start,
        end=end,
        cds=thick if thick[1] > thick[0] else None,
        exons=tuple(
            (start + o, start + o + s) for o, s in zip(offsets, sizes, strict=True)
        ),
    )


def write_transcripts(
    transcripts: Iterable[Transcript], path: Path, header: Sequence[str]
) -> None:
    """Sort, BGZF-compress and tabix-index the transcripts at ``path``; both
    files appear under their final names only once complete."""
    rows = sorted(transcripts, key=lambda t: (t.chrom, t.start, t.end, t.accession))
    partial = path.with_name(path.name + ".tmp.gz")
    try:
        with open(partial, "wb") as out:
            proc = popen(["bgzip", "-c"], stdin=subprocess.PIPE, stdout=out)
            assert proc.stdin is not None
            with proc.stdin as stdin:
                for line in header:
                    stdin.write(f"# {line}\n".encode())
                for transcript in rows:
                    stdin.write(bed12(transcript).encode())
            if proc.wait() != 0:
                raise WGSExtractError(f"bgzip failed writing {path}.")
        run_command(["tabix", "-f", "-p", "bed", str(partial)])
        Path(f"{partial}.tbi").replace(f"{path}.tbi")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
        Path(f"{partial}.tbi").unlink(missing_ok=True)


def transcripts_path(reflib: str | None, build: str) -> Path | None:
    """The library's indexed transcript models for ``build``, if installed."""
    if not reflib:
        return None
    name = TRANSCRIPTS_NAME.format(build=build)
    return next(
        (
            d / name
            for d in download_dirs(reflib)
            if (d / name).is_file() and (d / f"{name}.tbi").is_file()
        ),
        None,
    )


def delete_transcripts(reflib: str, build: str) -> list[Path]:
    """Remove ``build``'s transcript models; return those that remain."""
    name = TRANSCRIPTS_NAME.format(build=build)
    left = []
    for folder in download_dirs(reflib):
        for path in (folder / name, folder / f"{name}.tbi"):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                left.append(path)
    return left


def source_note(path: Path) -> str:
    """The provenance line ``ref gene-map`` wrote at the top of the models."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#"):
                break
            if line.startswith("# Transcripts"):
                return line[2:].strip()
    return ""


def read_transcripts(
    path: Path, chrom: str, start: int, end: int, symbol: str
) -> list[Transcript]:
    """``symbol``'s transcripts overlapping chrom:start-end (0-based,
    half-open), read through the tabix index."""
    result = run_command(
        ["tabix", str(path), f"{chrom}:{start + 1}-{end}"], capture_output=True
    )
    found = []
    for line in result.stdout.splitlines():
        if line and not line.startswith("#"):
            transcript = from_bed12(line)
            if transcript.symbol.upper() == symbol.upper():
                found.append(transcript)
    return found
