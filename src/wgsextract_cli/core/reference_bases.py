"""Verify point-reference alleles without extrapolating a gVCF block's anchor."""

from bisect import bisect_left
from collections.abc import Sequence
from pathlib import Path

from wgsextract_cli.core.reference_resolver import read_fai_lengths
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.core.variant_files import (
    match_chromosome_name,
    vcf_header_chromosomes,
)


def verified_reference_bases(
    gvcf: str,
    sites: Sequence[tuple[str, int]],
    work: Path,
    fasta: str | None,
) -> dict[tuple[str, int], str]:
    """Bases verified by an explicit REF, or a FASTA inside reference blocks.

    An explicit gVCF REF that disagrees with the FASTA invalidates the base.
    Without a FASTA only bases actually spelled in REF are verifiable.
    """
    if not sites:
        return {}
    bases: dict[tuple[str, int], str] = {}
    if fasta:
        names = read_fai_lengths(fasta)
        queries = {
            f"{alias}:{pos}-{pos}": (contig, pos)
            for contig, pos in sites
            if (alias := match_chromosome_name(contig, names))
        }
        regions = work / "reference-bases.txt"
        regions.write_text("".join(f"{r}\n" for r in queries), newline="\n")
        if queries:
            text = run_command(
                ["samtools", "faidx", "-r", str(regions), fasta], capture_output=True
            ).stdout
            key = None
            for line in text.splitlines():
                if line.startswith(">"):
                    key = queries.get(line[1:])
                elif key is not None:
                    bases[key] = bases.get(key, "") + line.upper()
    by_contig: dict[str, list[int]] = {}
    for contig, pos in sites:
        by_contig.setdefault(contig, []).append(pos)
    for positions in by_contig.values():
        positions.sort()
    header = run_command(["bcftools", "view", "-h", gvcf], capture_output=True).stdout
    declared = vcf_header_chromosomes(header)
    aliases = {
        alias: c for c in by_contig if (alias := match_chromosome_name(c, declared))
    }
    regions = work / "reference-bases.tsv"
    regions.write_text(
        "".join(
            f"{alias}\t{pos}\t{pos}\n"
            for alias, c in aliases.items()
            for pos in by_contig[c]
        ),
        newline="\n",
    )
    if not aliases:
        return bases
    records = run_command(
        ["bcftools", "query", "-R", str(regions), "-f", "%CHROM\t%POS\t%REF\n", gvcf],
        capture_output=True,
    ).stdout
    invalid = set()
    for line in records.splitlines():
        chrom, position, ref = line.split("\t")
        contig, start = aliases[chrom], int(position)
        positions = by_contig[contig]
        first = bisect_left(positions, start)
        last = bisect_left(positions, start + len(ref))
        for pos in positions[first:last]:
            key, base = (contig, pos), ref[pos - start].upper()
            if key in bases and bases[key] != base:
                invalid.add(key)
            elif not fasta or key in bases:
                bases[key] = base
    return {
        key: base
        for key, base in bases.items()
        if key not in invalid and base in {"A", "C", "G", "T"}
    }
