"""Write a long TSV of exact GRCh38 GTEx variant–gene–tissue associations."""

import csv
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from wgsextract_cli.core.gtex_data import SEQUENCE
from wgsextract_cli.core.utils import WGSExtractError, popen
from wgsextract_cli.core.vcf_build import primary_grch38_aliases

COLUMNS = (
    "chrom", "pos", "ref", "alt", "alt_index", "tissue", "gene_id", "gene_name",
    "credible_set", "pip", "afc",
)  # fmt: skip


def write_matches(
    vcf: str, output: Path, destination: Path, db: sqlite3.Connection
) -> dict[str, int]:
    aliases = primary_grch38_aliases()
    contigs: dict[str, bool] = {}
    records = matched_records = matched_alts = associations = 0
    replacing = destination.is_file()

    def open_staged(path: str, flags: int) -> int:
        return os.open(path, flags, 0o600 if replacing else 0o666)

    with (
        open(output, "x", encoding="utf-8", newline="", opener=open_staged) as stream,
        popen(
            ["bcftools", "query", "-f", r"%CHROM\t%POS\t%REF\t%ALT\n", vcf],
            stdout=subprocess.PIPE,
            text=True,
        ) as query,
    ):
        if replacing:
            shutil.copymode(destination, output)
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(COLUMNS)
        assert query.stdout is not None
        try:
            for line in query.stdout:
                records += 1
                chrom, pos_text, ref, alts = line.rstrip("\n").split("\t")
                alias = aliases.get(chrom.upper())
                contig = alias.name if alias else chrom
                if contig not in contigs:
                    contigs[contig] = (
                        db.execute(
                            "SELECT 1 FROM association WHERE chrom=? LIMIT 1", (contig,)
                        ).fetchone()
                        is not None
                    )
                if not contigs[contig] or not SEQUENCE.fullmatch(ref):
                    continue
                pos, ref = int(pos_text), ref.upper()
                before = matched_alts
                for index, alt in enumerate(alts.split(","), start=1):
                    if not SEQUENCE.fullmatch(alt):
                        continue
                    alt = alt.upper()
                    if ref == alt:
                        continue
                    hits = db.execute(
                        "SELECT tissue, gene, gene_name, cs, pip, afc "
                        "FROM association WHERE chrom=? AND pos=? AND ref=? AND alt=? "
                        "ORDER BY ordinal",
                        (contig, pos, ref, alt),
                    )
                    found = False
                    for hit in hits:
                        writer.writerow((chrom, pos, ref, alt, index, *hit))
                        found = True
                        associations += 1
                    matched_alts += found
                matched_records += matched_alts > before
            if query.wait() != 0:
                raise WGSExtractError(
                    "bcftools could not read the VCF for GTEx annotation."
                )
        finally:
            if query.poll() is None:
                query.terminate()
    return {
        "gtex_records": records,
        "gtex_matched_records": matched_records,
        "gtex_matched_alts": matched_alts,
        "gtex_associations": associations,
    }
