"""Build the bundled exon models of `vcf callability` (assets/report/exons_<build>.tsv.gz).

Usage (from the repository root):

    pixi run -e dev python scripts/build_report_exons.py --cache tmp/report-exon-sources

Downloads the pinned sources below into --cache (each checked against its
SHA-256) and writes, for hg38 and hg19, one row per exon of each built-in
report gene list's gene (assets/report/gene_lists.json): the exons of its
MANE Select (GRCh38) or RefSeq Select (GRCh37) transcript, numbered in
transcript order, with the coding part of each (empty for an untranslated
exon). The transcript choice is the one scripts/build_report_gene_lists.py
uses for the genes' spans.

Each row also lists the short-read limitations near the exon (within
MAX_FLANK bases): GIAB genome stratifications' low-mappability regions
(sequence 100 bp reads with up to 2 mismatches and 1 indel, or 250 bp reads
exactly, also match elsewhere) and segmental duplications (copies over 1 kb
at 90% identity or more), as 1-based inclusive intervals.

Sources and terms: NCBI RefSeq annotation is public domain (NCBI data
policy); the GIAB stratifications are NIST data "freely available for reuse
without embargo" (their README). No interpretation is copied: rows are
coordinates computed from those files.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import urllib.parse
import urllib.request
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "wgsextract_cli" / "assets" / "report"
NCBI = "https://ftp.ncbi.nlm.nih.gov/genomes/all/annotation_releases/9606"
GIAB = (
    "https://ftp-trace.ncbi.nlm.nih.gov/ReferenceSamples/giab/release/"
    "genome-stratifications/v3.6"
)
# The most flanking sequence `vcf callability --flank` may assess, so the
# limitation intervals cover it.
MAX_FLANK = 50


class _Source(TypedDict):
    annotation: str
    gff: tuple[str, str]
    tag: str
    limitations: dict[str, tuple[str, str]]


SOURCES: dict[str, _Source] = {
    "hg38": {
        "annotation": "NCBI RefSeq annotation GCF_000001405.40-RS_2025_08 (GRCh38.p14)",
        "gff": (
            f"{NCBI}/GCF_000001405.40-RS_2025_08/GCF_000001405.40_GRCh38.p14_genomic.gff.gz",
            "4920f0eae7e2197c50b67a201e06d657387137b49dd60f474b4f1d5b29334051",
        ),
        "tag": "MANE Select",
        "limitations": {
            "low_mappability": (
                f"{GIAB}/GRCh38@all/Mappability/GRCh38_lowmappabilityall.bed.gz",
                "38707879ab51dcee3dd3331c082e2d9552a9f9d487d4de84f9ebae5b5c556d56",
            ),
            "segmental_duplication": (
                f"{GIAB}/GRCh38@all/SegmentalDuplications/GRCh38_segdups.bed.gz",
                "09ac029a7d0d681a9f0ed6581a6bc10fc708a834bc3f91c46a9bef504f6055c5",
            ),
        },
    },
    "hg19": {
        "annotation": "NCBI RefSeq annotation release 105.20220307 (GRCh37.p13)",
        "gff": (
            f"{NCBI}/105.20220307/GCF_000001405.25_GRCh37.p13/GCF_000001405.25_GRCh37.p13_genomic.gff.gz",
            "ba7228449502b41694c268b16192fd483d05c1f0d7af22b7715c750d7227bae0",
        ),
        "tag": "RefSeq Select",
        "limitations": {
            "low_mappability": (
                f"{GIAB}/GRCh37@all/Mappability/GRCh37_lowmappabilityall.bed.gz",
                "139d9b75140bf463cced59f0b8efe40f08fddcefce37b8b9a4b51fe2f48018a9",
            ),
            "segmental_duplication": (
                f"{GIAB}/GRCh37@all/SegmentalDuplications/GRCh37_segdups.bed.gz",
                "59a73c1381e066c51b742cd8213f018888318371a5d9389651bd671912cd79be",
            ),
        },
    },
}
COLUMNS = ("gene", "transcript", "tag", "strand", "exon", "exons", "chrom",
           "start", "end", "cds_start", "cds_end", "limitations")  # fmt: skip
Interval = tuple[int, int]


def fetch(url: str, sha256: str, cache: Path, *, download: bool = True) -> Path:
    path = cache / url.rsplit("/", 1)[1]
    if not path.exists():
        if not download:
            raise SystemExit(
                f"{path} is missing; rerun with --download to fetch {url}."
            )
        print(f"downloading {url}")
        partial = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(url, partial)
        partial.rename(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != sha256:
        raise SystemExit(f"{path}: sha256 {digest}, expected {sha256}")
    return path


def _chromosome(accession: str) -> str | None:
    """chr1..chr22, chrX, chrY for RefSeq NC_0000NN chromosome accessions."""
    match = re.fullmatch(r"NC_0000(\d\d)\.\d+", accession)
    if not match:
        return None
    number = int(match.group(1))
    return {23: "chrX", 24: "chrY"}.get(number, f"chr{number}")


def _attrs(text: str) -> dict[str, str]:
    return {
        key: urllib.parse.unquote(value)
        for key, _, value in (item.partition("=") for item in text.split(";"))
    }


@dataclass
class Transcript:
    gene: str
    accession: str
    chrom: str
    strand: str
    exons: dict[int, Interval] = field(default_factory=dict)
    cds: list[Interval] = field(default_factory=list)


def transcripts(
    gff: Path, symbols: set[str], tag: str, *, exclude_y: bool = False
) -> tuple[dict[str, Transcript], dict[str, int]]:
    """Each symbol's ``tag`` transcript on the primary chromosomes, and the
    chromosomes' lengths."""
    lengths: dict[str, int] = {}
    chosen: dict[str, Transcript] = {}
    # NCBI's GFF lists a transcript before its exons and CDS.
    by_id: dict[str, Transcript] = {}
    with gzip.open(gff, "rt") as stream:
        for line in stream:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 9 or (chrom := _chromosome(cols[0])) is None:
                continue
            if exclude_y and chrom == "chrY":
                continue
            if cols[2] == "region":
                # The first region row is the whole chromosome; later ones
                # (PARs, for example) lie inside it.
                lengths[chrom] = max(lengths.get(chrom, 0), int(cols[4]))
            elif cols[2] == "mRNA":
                attrs = _attrs(cols[8])
                gene = attrs.get("gene", "")
                if gene in symbols and tag in attrs.get("tag", "").split(","):
                    if gene in chosen:
                        raise SystemExit(f"{gene}: two {tag} transcripts")
                    chosen[gene] = Transcript(
                        gene, attrs["transcript_id"], chrom, cols[6]
                    )
                    by_id[attrs["ID"]] = chosen[gene]
            elif cols[2] in ("exon", "CDS"):
                parent = re.search(r"(?:^|;)Parent=([^;]+)", cols[8])
                found = by_id.get(parent.group(1)) if parent else None
                if found is None:
                    continue
                span = (int(cols[3]), int(cols[4]))
                if cols[2] == "CDS":
                    found.cds.append(span)
                else:
                    exon_id = _attrs(cols[8])["ID"]
                    found.exons[int(exon_id.rsplit("-", 1)[1])] = span
    missing = sorted(symbols - set(chosen))
    if missing:
        raise SystemExit(f"no {tag} transcript: {missing}")
    for t in chosen.values():
        numbers = sorted(t.exons)
        if numbers != list(range(1, len(numbers) + 1)) or not t.cds:
            raise SystemExit(f"{t.gene} {t.accession}: incomplete exons or CDS")
    return chosen, lengths


def intervals(
    bed: Path, wanted: Iterable[tuple[str, int, int]]
) -> dict[str, list[Interval]]:
    """The BED's intervals (1-based, inclusive) within ``wanted`` windows."""
    windows: dict[str, list[Interval]] = {}
    for chrom, start, end in wanted:
        windows.setdefault(chrom, []).append((start, end))
    found: dict[str, list[Interval]] = {}
    with gzip.open(bed, "rt") as stream:
        for line in stream:
            cols = line.split("\t")
            # GIAB's GRCh37 files name chromosomes without "chr".
            chrom = cols[0] if cols[0].startswith("chr") else f"chr{cols[0]}"
            if chrom not in windows:
                continue
            start, end = int(cols[1]) + 1, int(cols[2])
            for low, high in windows[chrom]:
                if start <= high and end >= low:
                    found.setdefault(chrom, []).append(
                        (max(start, low), min(end, high))
                    )
    return found


def rows(
    found: dict[str, Transcript], limits: dict[str, dict[str, list[Interval]]]
) -> list[tuple[object, ...]]:
    indexed = {
        kind: {chrom: _IntervalIndex(spans) for chrom, spans in by_chrom.items()}
        for kind, by_chrom in limits.items()
    }
    out: list[tuple[object, ...]] = []
    for t in sorted(found.values(), key=lambda t: t.gene):
        for number, (start, end) in sorted(t.exons.items()):
            coding = [
                (max(start, s), min(end, e))
                for s, e in t.cds
                if s <= end and e >= start
            ]
            cds_start = min((s for s, _ in coding), default=None)
            cds_end = max((e for _, e in coding), default=None)
            low, high = start - MAX_FLANK, end + MAX_FLANK
            near = sorted(
                f"{kind}:{max(s, low)}-{min(e, high)}"
                for kind, by_chrom in indexed.items()
                if (index := by_chrom.get(t.chrom)) is not None
                for s, e in index.overlap(low, high)
            )
            out.append((t.gene, t.accession, "", t.strand, number, len(t.exons),
                        t.chrom, start, end, cds_start or "", cds_end or "",
                        ";".join(near)))  # fmt: skip
    return out


class _IntervalIndex:
    def __init__(self, spans: Iterable[Interval]) -> None:
        self.spans = sorted(spans)
        self.starts = [start for start, _ in self.spans]
        self.max_ends: list[int] = []
        high = 0
        for _, end in self.spans:
            high = max(high, end)
            self.max_ends.append(high)

    def overlap(self, start: int, end: int) -> Iterable[Interval]:
        left = bisect_left(self.max_ends, start)
        right = bisect_right(self.starts, end)
        return (span for span in self.spans[left:right] if span[1] >= start)


def write(path: Path, table: list[tuple[object, ...]], lengths: dict[str, int],
          notes: list[str]) -> None:  # fmt: skip
    chroms = sorted({str(r[6]) for r in table}, key=lambda c: (len(c), c))
    header = [f"# {note}" for note in notes]
    header += [f"#contig\t{c}\t{lengths[c]}" for c in chroms]
    header.append("\t".join(COLUMNS))
    body = [*header, *("\t".join(map(str, r)) for r in table)]
    with path.open("wb") as raw:
        filename = path.name.removesuffix(".gz")
        with gzip.GzipFile(
            filename, "wb", fileobj=raw, compresslevel=9, mtime=0
        ) as out:
            out.write(("\n".join(body) + "\n").encode())
    print(f"{path}: {len(table)} exons, {path.stat().st_size} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--cache", type=Path, default=ROOT / "tmp" / "report-exon-sources"
    )
    parser.add_argument(
        "--genome-wide-mane",
        type=Path,
        metavar="TSV.GZ",
        help="Write the MANE Select asset for autosomes and chrX to this path.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Fetch missing pinned NCBI and GIAB public inputs for --genome-wide-mane.",
    )
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    genome_wide = args.genome_wide_mane is not None
    builds: tuple[str, ...]
    if genome_wide:
        symbols = _mane_symbols()
        builds = ("hg38",)
    else:
        lists = json.loads((OUT / "gene_lists.json").read_text(encoding="utf-8"))
        symbols = {g for meta in lists["lists"].values() for g in meta["genes"]}
        builds = tuple(SOURCES)
    download = args.download or not genome_wide
    for build in builds:
        src = SOURCES[build]
        url, sha = src["gff"]
        tag = src["tag"]
        found, lengths = transcripts(
            fetch(url, sha, args.cache, download=download),
            symbols,
            tag,
            exclude_y=genome_wide,
        )
        windows = [
            (t.chrom, s - MAX_FLANK, e + MAX_FLANK)
            for t in found.values()
            for s, e in t.exons.values()
        ]
        limits = {}
        for kind, (u, h) in src["limitations"].items():
            bed = fetch(u, h, args.cache, download=download)
            regions = ((chrom, 1, size) for chrom, size in lengths.items())
            limits[kind] = intervals(bed, regions if genome_wide else windows)
        table = [(*r[:2], tag, *r[3:]) for r in rows(found, limits)]
        selection = (
            f"autosomal and chrX genes with a {tag} transcript"
            if genome_wide
            else f"each built-in report gene list gene's {tag} transcript"
        )
        notes = [
            f"Exons of {selection}, from {src['annotation']}; public domain.",
            f"Source: {url} (sha256 {sha}).",
            "limitations: GIAB genome stratifications v3.6 low_mappability "
            "(lowmappabilityall) and segmental_duplication (segdups) intervals "
            f"within {MAX_FLANK} bp of the exon; NIST data, freely reusable.",
            *(f"Source: {u} (sha256 {h})." for u, h in src["limitations"].values()),
            "Coordinates are 1-based, inclusive; cds_start/cds_end are empty for an "
            "untranslated exon. Built by scripts/build_report_exons.py.",
        ]
        path = args.genome_wide_mane or OUT / f"exons_{build}.tsv.gz"
        write(path, table, lengths, notes)


def _mane_symbols() -> set[str]:
    path = OUT.parent / "panel" / "genes_hg38.tsv.gz"
    with gzip.open(path, "rt") as stream:
        fields = (
            line.rstrip("\n").split("\t") for line in stream if not line.startswith("#")
        )
        columns = next(fields)
        return {
            row["symbol"]
            for values in fields
            if (row := dict(zip(columns, values, strict=True)))["cds_basis"]
            == "mane_select"
            and row["chrom"] != "chrY"
        }


if __name__ == "__main__":
    main()
