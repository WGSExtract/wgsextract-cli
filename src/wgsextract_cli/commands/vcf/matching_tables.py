"""Project indexed allele tables into the resource normalization stream."""

import gzip
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from wgsextract_cli.core.utils import run_command
from wgsextract_cli.core.variant_files import match_chromosome_name, vcf_chromosomes

from .resource_ref_check import warn_skipped_contigs


def select_snv_rows(source: str, query: str, work: str) -> Path:
    regions = Path(work, "snv-regions.tsv")
    available = run_command(
        ["tabix", "-l", source], capture_output=True
    ).stdout.splitlines()
    query_names = {
        c: match_chromosome_name(c, available) for c in vcf_chromosomes(query)
    }
    with gzip.open(query, "rt") as records, regions.open("w", newline="\n") as loci:
        for line in records:
            if line.startswith("#"):
                continue
            row = line.split("\t")
            if len(row[3]) == len(row[4]) == 1 and set(row[3] + row[4]) <= set("ACGT"):
                chrom = query_names.get(row[0])
                if chrom:
                    loci.write(f"{chrom}\t{row[1]}\t{row[1]}\n")
    subset = Path(work, "snv-scores.tsv")
    with subset.open("w", newline="\n") as selected:
        if regions.stat().st_size:
            run_command(["tabix", "-R", str(regions), source], stdout=selected)
    return subset


def write_table(
    source: str,
    fields: list[str],
    headers: Sequence[str],
    lengths: dict[str, int],
    subset: Path | None,
    handle: TextIO,
) -> tuple[int, bool]:
    tags = [
        (i, c.removeprefix("INFO/"))
        for i, c in enumerate(fields)
        if c.startswith("INFO/")
    ]
    handle.write("##fileformat=VCFv4.2\n")
    handle.writelines(f"##contig=<ID={c},length={n}>\n" for c, n in lengths.items())
    handle.writelines(h + "\n" for h in headers)
    handle.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
    mapped_names: dict[str, str | None] = {}
    chrom_col, pos_col, ref_col, alt_col = (
        fields.index(c) for c in ("CHROM", "POS", "REF", "ALT")
    )
    total = skipped = 0
    has_multiallelic = False
    with subset.open() if subset else gzip.open(source, "rt") as records:
        for line in records:
            if line.startswith("#"):
                continue
            row = line.rstrip("\n").split("\t")
            chrom = row[chrom_col]
            if chrom not in mapped_names:
                mapped_names[chrom] = match_chromosome_name(chrom, lengths)
            mapped = mapped_names[chrom]
            if mapped is None:
                skipped += 1
                continue
            info = ";".join(f"{tag}={row[i]}" if row[i] else tag for i, tag in tags)
            alt = row[alt_col]
            has_multiallelic |= "," in alt
            total += 1
            handle.write(
                f"{mapped}\t{row[pos_col]}\t.\t{row[ref_col]}\t{alt}\t.\t.\t{info or '.'}\n"
            )
    warn_skipped_contigs(
        {c for c, target in mapped_names.items() if target is None}, skipped
    )
    return total, has_multiallelic
