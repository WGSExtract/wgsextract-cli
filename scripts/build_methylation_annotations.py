"""Build bundled promoter TSSs and hs1 islands from pinned RefSeq/UCSC data.

Run via Pixi on Linux/macOS (pyBigWig is needed by this builder only):
    pixi run python -m scripts.build_methylation_annotations --cache out/methylation
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from scripts.build_panel_annotations import _NCBI, CHROMS, ROOT, SOURCES, fetch
from wgsextract_cli.core.methylation_annotations import (
    BUILDS,
    HS1_ISLAND_SHA256,
    UCSC_HS1,
)

OUT = ROOT / "src" / "wgsextract_cli" / "assets" / "methylation"
ChromosomeRow = tuple[str, int, list[str]]


def primary_chromosomes(report: Iterable[str]) -> list[ChromosomeRow]:
    rows = []
    for line in report:
        if line.startswith("#"):
            continue
        _, role, _, _, genbank, relation, refseq, _, length, chrom = line.strip().split(
            "\t"
        )
        if role != "assembled-molecule" or chrom not in CHROMS:
            continue
        aliases = (
            [chrom, chrom.removeprefix("chr")]
            if chrom != "chrM"
            else ["chrM", "MT", "M", "chrMT"]
        )
        if refseq != "na":
            aliases.append(refseq)
        if genbank != "na" and (relation == "=" or refseq == "na"):
            aliases.append(genbank)
        rows.append((chrom, int(length), aliases))
    return rows


@dataclass(frozen=True)
class Transcript:
    parent: str
    identifier: str
    accessioned: bool
    strand: str
    tss: int
    priority: int


def selected_transcripts(lines: Iterable[str], refs: dict[str, str]) -> list[str]:
    """Use direct, exonic RefSeq transcripts of each gene locus."""
    genes: dict[tuple[str, str], tuple[str, str]] = {}
    transcripts: dict[tuple[str, str], Transcript] = {}
    lengths: dict[tuple[str, str], int] = defaultdict(int)
    for line in lines:
        f = line.split("\t", 8)
        if len(f) < 9 or f[0] not in refs:
            continue
        chrom, feature = refs[f[0]], f[2]
        attrs = dict(
            part.split("=", 1) for part in f[8].rstrip().split(";") if "=" in part
        )
        if feature in ("gene", "pseudogene"):
            gene_id = next(
                (
                    x.removeprefix("GeneID:")
                    for x in attrs.get("Dbxref", "").split(",")
                    if x.startswith("GeneID:")
                ),
                "",
            )
            genes[chrom, attrs["ID"]] = (
                unquote(attrs.get("gene") or attrs["Name"]),
                gene_id,
            )
        elif feature == "exon":
            for parent in attrs.get("Parent", "").split(","):
                lengths[chrom, parent] += int(f[4]) - int(f[3]) + 1
        elif (
            "ID" in attrs
            and f[6] in {"+", "-"}
            and (
                attrs.get("transcript_id")
                or feature.endswith("RNA")
                or feature == "transcript"
            )
        ):
            accession = attrs.get("transcript_id")
            tags = set(attrs.get("tag", "").split(","))
            priority = (
                0 if "MANE Select" in tags else 1 if "RefSeq Select" in tags else 2
            )
            strand = f[6]
            tss = int(f[3]) - 1 if strand == "+" else int(f[4])
            transcripts[chrom, attrs["ID"]] = Transcript(
                attrs.get("Parent", ""),
                accession or attrs["ID"],
                bool(accession),
                strand,
                tss,
                priority,
            )

    chosen: dict[
        tuple[str, str], tuple[tuple[int, int, bool, str, int, str], Transcript]
    ] = {}
    for (chrom, feature_id), tx in transcripts.items():
        locus = chrom, tx.parent
        size = lengths[chrom, feature_id]
        if not size or locus not in genes:
            continue
        rank = (
            tx.priority,
            -size if tx.priority == 2 else 0,
            not tx.accessioned,
            tx.identifier,
            tx.tss,
            tx.strand,
        )
        previous = chosen.get(locus)
        if previous is None or rank < previous[0]:
            chosen[locus] = rank, tx

    rows = []
    for locus, (symbol, gene_id) in genes.items():
        if locus in chosen:
            tx = chosen[locus][1]
            fields = tx.identifier, tx.strand, str(tx.tss)
        else:
            fields = "", "", ""
        rows.append("\t".join((locus[0], symbol, gene_id, *fields)) + "\n")
    return sorted(set(rows))


def write_gzip(path: Path, lines: Iterable[str]) -> None:
    with (
        path.open("wb") as handle,
        gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, filename="") as zipped,
    ):
        for line in lines:
            zipped.write(line.encode())


def hs1_bed(
    path: Path, aliases: dict[str, str], lengths: dict[str, int]
) -> Iterable[str]:
    import pyBigWig

    bb = pyBigWig.open(str(path))
    try:
        for accession, length in sorted(bb.chroms().items()):
            chrom = aliases[accession]
            if length != lengths[chrom]:
                raise ValueError(
                    f"hs1 {accession} length differs from the assembly report"
                )
            for start, end, _ in bb.entries(accession, 0, length) or ():
                yield f"{chrom}\t{start}\t{end}\n"
    finally:
        bb.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for build in BUILDS:
        source = SOURCES[build]
        base = f"{_NCBI}/{source['release']}/{source['prefix']}"
        gff_url, report_url = f"{base}_genomic.gff.gz", f"{base}_assembly_report.txt"
        gff = fetch(gff_url, source["gff"], args.cache)
        report = fetch(report_url, source["report"], args.cache)
        rows = primary_chromosomes(report.read_text(encoding="utf-8").splitlines())
        refs = {alias: chrom for chrom, _, aliases in rows for alias in aliases}
        with gzip.open(gff, "rt", encoding="utf-8", newline="") as lines:
            transcripts = selected_transcripts(lines, refs)
        # Split the snapshot to keep each committed asset under 500 KiB.
        split = len(transcripts) // 2
        for part, block in enumerate((transcripts[:split], transcripts[split:]), 1):
            write_gzip(OUT / f"{build}-promoters-{part}.tsv.gz", block)
        metadata = {
            "assembly_report": {"url": report_url, "sha256": source["report"]},
            "promoters": {
                "source": "NCBI RefSeq",
                "version": source["release"],
                "url": gff_url,
                "sha256": source["gff"],
            },
            "chromosomes": [
                (chrom, length, *aliases) for chrom, length, aliases in rows
            ],
        }
        (OUT / f"{build}-chromosomes.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        if build == "t2t":
            bb = fetch(UCSC_HS1, HS1_ISLAND_SHA256, args.cache)
            write_gzip(
                OUT / "t2t-islands.bed.gz",
                hs1_bed(bb, refs, {chrom: length for chrom, length, *_ in rows}),
            )


if __name__ == "__main__":
    main()
