"""Build the bundled long-read repeat catalog of `typing str`
(src/wgsextract_cli/commands/genotyping/str_catalog/).

Usage (from the repository root):

    pixi run -e dev python scripts/build_str_catalog.py --cache tmp/str-sources

Sources are downloaded into --cache, pinned by commit and SHA-256 (SOURCES).

The long-read callers size the same repeats as ExpansionHunter, in the same
units, so one table of STRchive ranges (``repeats.THRESHOLDS``) classifies
every caller's counts. For each repeat of ExpansionHunter's catalog (one row
per VariantId, flanking repeats included), the script takes

1. ExpansionHunter's reference region: the tract the thresholds count;
2. the motifs from STRchive's LongTR catalog for the same gene when they are
   plain bases and not rotations of the unit (RFC1's AAGGG, ACAGG, ...), after
   ExpansionHunter's repeat unit, with IUPAC codes resolved by
   MOTIF_OVERRIDES. The first motif is the one whose length counts the repeat.
   STRchive motifs on the other strand (rotations of the unit's reverse
   complement) are left out: NIPA1's region is inverted between GRCh37 and
   GRCh38, so its reference reads (CGC)n on hg19 and (GCG)n on hg38, and
   STRchive's hg19 catalog carries over the hg38 strand's CGG.

It writes, per build, ``<build>.longtr.bed`` (LongTR's 1-based start, end,
comma-separated motifs, name) and ``<build>.strkit.bed`` (0-based BED with
``ID=...;MOTIF=...``, the motifs merged into one IUPAC motif), plus
provenance.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path

OUT = Path("src/wgsextract_cli/commands/genotyping/str_catalog")
_EH = "https://raw.githubusercontent.com/Illumina/ExpansionHunter/9f5db1d4ab72016b2d93330b9792842d71582622/variant_catalog"
_STRCHIVE = "https://raw.githubusercontent.com/dashnowlab/STRchive/e964fa88df3b2189ee478cbd6eaad15bb28e3a0a/data/catalogs"

SOURCES: dict[str, dict[str, str]] = {
    "expansionhunter_hg38": {
        "url": f"{_EH}/hg38/variant_catalog.json",
        "sha256": "6c0d30ad3c23246be95b63e267790ad9d0bff635a42772f355213b05fbed430c",
        "license": "Apache-2.0 (Illumina/ExpansionHunter v5.0.0)",
        "cite": "Dolzhenko et al. 2019, Bioinformatics 35:4754-4756",
    },
    "expansionhunter_hg19": {
        "url": f"{_EH}/hg19/variant_catalog.json",
        "sha256": "eeb52041f58100f630044cb362718119f806bcd3c295ea2308eb9dcbda8307ea",
        "license": "Apache-2.0 (Illumina/ExpansionHunter v5.0.0)",
        "cite": "Dolzhenko et al. 2019, Bioinformatics 35:4754-4756",
    },
    "strchive_hg38": {
        "url": f"{_STRCHIVE}/STRchive-disease-loci.hg38.longTR.bed",
        "sha256": "72c0d8ba90d2ae0f2e5fe7122d93e92d47a00de239ad5c6912e8957de83ae4f6",
        "license": "MIT (dashnowlab/STRchive v2.28.0)",
        "cite": "Hiatt et al. 2025, Genome Medicine 17:29 (STRchive), https://strchive.org",
    },
    "strchive_hg19": {
        "url": f"{_STRCHIVE}/STRchive-disease-loci.hg19.longTR.bed",
        "sha256": "60575dd23d620a2d6e510c2ef0cbde237c959bc58dcc334a857b652742d210b3",
        "license": "MIT (dashnowlab/STRchive v2.28.0)",
        "cite": "Hiatt et al. 2025, Genome Medicine 17:29 (STRchive), https://strchive.org",
    },
}

# ExpansionHunter units with IUPAC codes, as plain bases. Only the motif
# length counts the repeat; the bases guide the callers' alignment.
MOTIF_OVERRIDES = {"PABPN1": "GCG", "PHOX2B": "GCG", "RFC1": "AAAAG"}
# ExpansionHunter's gene names where STRchive's differ.
STRCHIVE_GENES = {"C9ORF72": "C9orf72", "NOTCH2NL": "NOTCH2NLC"}


def fetch(name: str, cache: Path) -> bytes:
    source = SOURCES[name]
    path = cache / name
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(source["url"]) as response:
            path.write_bytes(response.read())
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != source["sha256"]:
        raise SystemExit(f"{name}: SHA-256 {digest}, expected {source['sha256']}")
    return data


def _revcomp(motif: str) -> str:
    return motif[::-1].translate(str.maketrans("ACGT", "TGCA"))


_IUPAC = {
    frozenset(bases): code
    for code, bases in {
        "A": "A", "C": "C", "G": "G", "T": "T", "R": "AG", "Y": "CT", "S": "CG",
        "W": "AT", "K": "GT", "M": "AC", "B": "CGT", "D": "AGT", "H": "ACT",
        "V": "ACG", "N": "ACGT",
    }.items()
}  # fmt: skip


def iupac(motifs: list[str]) -> str:
    """One motif matching every one of ``motifs`` (of equal length), for
    STRkit, which takes a single motif per locus but reads IUPAC codes:
    RFC1's AAAAG, AAGGG, ACAGG, ... become MMRRG."""
    return "".join(_IUPAC[frozenset(column)] for column in zip(*motifs, strict=True))


def _plain(motifs: list[str]) -> bool:
    return all(re.fullmatch("[ACGT]+", motif) for motif in motifs)


def build(build_name: str, cache: Path) -> list[tuple[str, int, int, list[str], str]]:
    catalog = json.loads(fetch(f"expansionhunter_{build_name}", cache))
    strchive = {}
    for line in fetch(f"strchive_{build_name}", cache).decode().splitlines():
        _chrom, _start, _end, motifs, name = line.split("\t")
        strchive[name.split("_", 1)[1]] = motifs.split(",")
    rows = []
    for locus in catalog:
        ids = locus.get("VariantId") or [locus["LocusId"]]
        regions = locus["ReferenceRegion"]
        regions = regions if isinstance(regions, list) else [regions]
        units = re.findall(r"\(([A-Z]+)\)", locus["LocusStructure"])
        for variant_id, region, unit in zip(ids, regions, units, strict=True):
            chrom, span = region.split(":")
            start, end = (int(x) for x in span.split("-"))
            unit = MOTIF_OVERRIDES.get(variant_id, unit)
            motifs = [unit]
            if variant_id == locus["LocusId"]:
                extra = strchive.get(STRCHIVE_GENES.get(variant_id, variant_id), [])
                if _plain(extra):
                    motifs += [
                        m for m in extra
                        if len(m) == len(unit)
                        and m not in unit * 2
                        and _revcomp(m) not in unit * 2
                    ]  # fmt: skip
            if not _plain(motifs):
                raise SystemExit(f"{variant_id}: motif {unit} needs an override")
            rows.append((chrom, start, end, motifs, variant_id))
    return sorted(rows, key=lambda r: (r[0], r[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=Path("tmp/str-sources"))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for build_name in ("hg38", "hg19"):
        rows = build(build_name, args.cache)
        with open(OUT / f"{build_name}.longtr.bed", "w", newline="\n") as f:
            for chrom, start, end, motifs, name in rows:
                f.write(f"{chrom}\t{start + 1}\t{end}\t{','.join(motifs)}\t{name}\n")
        with open(OUT / f"{build_name}.strkit.bed", "w", newline="\n") as f:
            for chrom, start, end, motifs, name in rows:
                f.write(f"{chrom}\t{start}\t{end}\tID={name};MOTIF={iupac(motifs)}\n")
    provenance = {
        "generator": "scripts/build_str_catalog.py",
        "sources": SOURCES,
        "files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(OUT.glob("*.bed"))
        },
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
