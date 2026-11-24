#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the report's reviewed mtDNA variant table (assets/report/mt_variants.json).

Usage (from the repository root; needs the network and bcftools):

    pixi run python scripts/build_report_mt_variants.py --release 20261115

The table is a minimal factual curation, rebuilt by rule rather than copied
from any curated database (MITOMAP is not used):

- disease: every mtDNA SNV in the pinned ClinVar GRCh38 VCF release whose
  aggregate germline classification is Pathogenic (with or without
  drug_response), reviewed by an expert panel or practice guideline, without
  conflicting classifications. CPIC's MT-RNR1 alleles below are listed under
  aminoglycoside instead.
- aminoglycoside: the three MT-RNR1 alleles CPIC classes as "increased risk of
  aminoglycoside-induced hearing loss" (McDermott et al. 2022, PMID 34032273,
  Table 1), with ClinVar's record for each from the same release.

Genes are the ClinVar GENEINFO symbols whose NC_012920.1 gene span (NCBI's
feature table) contains the position. ClinVar and NCBI data carry no use
restrictions (NCBI policy, recorded under "terms"); CPIC content is CC0 1.0.
Only facts are kept: positions, alleles, identifiers, classifications,
review statuses and condition names.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from wgsextract_cli.core.ref_library.mitochondrial import (  # noqa: E402
    pinned_sequences,
)

OUTPUT = ROOT / "src" / "wgsextract_cli" / "assets" / "report" / "mt_variants.json"
CLINVAR = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar_{release}.vcf.gz"
FEATURES = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    "?db=nuccore&id=NC_012920.1&rettype=ft&retmode=text"
)
NCBI_TERMS = "https://www.ncbi.nlm.nih.gov/home/about/policies/"
CPIC = {
    "citation": "McDermott JH, Wolf J, Hoshitsuki K, et al. Clinical "
    "Pharmacogenetics Implementation Consortium Guideline for the Use of "
    "Aminoglycosides Based on MT-RNR1 Genotype. Clin Pharmacol Ther "
    "2022;111(2):366-372. doi:10.1002/cpt.2309. PMID 34032273",
    "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC8613315/",
    "licence": "CC0-1.0",
    "terms": "https://github.com/cpicpgx/cpic-data/blob/"
    "5ee5faf059211ff4636573aedda0eafc94deb1fc/LICENSE.md",
    "heteroplasmy": "CPIC found insufficient evidence for a heteroplasmy level "
    "at which aminoglycosides become safe, and recommends following the "
    "guidance for a homoplasmic variant whenever a relevant MT-RNR1 variant "
    "is detected.",
}
# CPIC Table 1: increased-risk MT-RNR1 alleles and the evidence level the
# guideline assigns each.
CPIC_ALLELES = {
    (1095, "T", "C"): "moderate",
    (1494, "C", "T"): "high",
    (1555, "A", "G"): "high",
}
CPIC_PHENOTYPE = "increased risk of aminoglycoside-induced hearing loss"
REVIEWED = ("reviewed_by_expert_panel", "practice_guideline")
IGNORED_CONDITIONS = {"not provided", "not specified"}


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "wgsextract"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return str(response.read().decode())


def gene_spans() -> dict[str, tuple[int, int]]:
    """NC_012920.1 genes by NCBI GeneID: (start, end), 1-based inclusive."""
    spans: dict[str, tuple[int, int]] = {}
    span: tuple[int, int] | None = None
    for line in _fetch(FEATURES).splitlines():
        fields = line.split("\t")
        if len(fields) >= 3 and fields[0] and fields[2] == "gene":
            a, b = int(fields[0]), int(fields[1])
            span = (min(a, b), max(a, b))
        elif span and len(fields) >= 5 and fields[3] == "db_xref":
            if fields[4].startswith("GeneID:"):
                spans[fields[4].removeprefix("GeneID:")] = span
        elif fields[0]:
            span = None
    return spans


def clinvar_records(url: str) -> tuple[str, list[dict[str, object]]]:
    # bcftools saves the remote index in its working directory.
    work = ROOT / "out" / "build_report_mt_variants"
    work.mkdir(parents=True, exist_ok=True)
    header = subprocess.run(
        ["bcftools", "view", "-h", url],
        check=True,
        capture_output=True,
        text=True,
        cwd=work,
    ).stdout
    file_date = next(
        line.split("=", 1)[1]
        for line in header.splitlines()
        if line.startswith("##fileDate=")
    )
    body = subprocess.run(
        ["bcftools", "view", "-H", "-r", "MT", url],
        check=True,
        capture_output=True,
        text=True,
        cwd=work,
    ).stdout
    records = []
    for line in body.splitlines():
        _chrom, pos, vid, ref, alt, _q, _f, info = line.split("\t")[:8]
        fields = dict(kv.partition("=")[::2] for kv in info.split(";"))
        records.append({"pos": int(pos), "id": vid, "ref": ref, "alt": alt, **fields})
    return file_date, records


def _genes(record: dict[str, object], spans: dict[str, tuple[int, int]]) -> str:
    pos = int(str(record["pos"]))
    symbols = []
    for pair in str(record.get("GENEINFO") or "").split("|"):
        symbol, _, gene_id = pair.partition(":")
        start, end = spans.get(gene_id, (0, -1))
        if start <= pos <= end and symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        raise SystemExit(f"m.{pos}: no GENEINFO gene spans the position")
    return "/".join(symbols)


def _entry(
    record: dict[str, object], group: str, spans: dict[str, tuple[int, int]]
) -> dict[str, object]:
    pos, ref, alt = int(str(record["pos"])), str(record["ref"]), str(record["alt"])
    conditions = [
        name.replace("_", " ")
        for name in str(record.get("CLNDN") or "").split("|")
        if name and name.replace("_", " ") not in IGNORED_CONDITIONS
    ]
    entry: dict[str, object] = {
        "group": group,
        "gene": "MT-RNR1" if group == "aminoglycoside" else _genes(record, spans),
        "hgvs": f"m.{pos}{ref}>{alt}",
        "pos": pos,
        "ref": ref,
        "alt": alt,
        "clinvar": {
            "variation_id": str(record["id"]),
            "classification": str(record.get("CLNSIG") or ""),
            "review_status": str(record.get("CLNREVSTAT") or ""),
            "conditions": conditions,
        },
    }
    if group == "aminoglycoside":
        entry["cpic"] = {
            "phenotype": CPIC_PHENOTYPE,
            "evidence": CPIC_ALLELES[(pos, ref, alt)],
        }
    return entry


def build(release: str) -> dict[str, object]:
    url = CLINVAR.format(release=release)
    md5 = _fetch(f"{url}.md5").split()[0]
    file_date, records = clinvar_records(url)
    spans = gene_spans()
    variants = []
    for record in records:
        key = (int(str(record["pos"])), str(record["ref"]), str(record["alt"]))
        snv = len(key[1]) == len(key[2]) == 1
        if key in CPIC_ALLELES:
            variants.append(_entry(record, "aminoglycoside", spans))
        elif (
            snv
            and str(record.get("CLNSIG") or "").split("|")[0] == "Pathogenic"
            and str(record.get("CLNREVSTAT") or "") in REVIEWED
            and not record.get("CLNSIGCONF")
        ):
            variants.append(_entry(record, "disease", spans))
    rcrs = pinned_sequences()[0]
    for v in variants:
        if rcrs[int(str(v["pos"])) - 1] != v["ref"]:
            raise SystemExit(f"{v['hgvs']}: REF is not the rCRS base")
    found = {(v["pos"], v["ref"], v["alt"]) for v in variants}
    if missing := set(CPIC_ALLELES) - found:
        raise SystemExit(f"ClinVar {release} lacks CPIC alleles {sorted(missing)}")
    variants.sort(key=lambda v: (v["group"] != "aminoglycoside", v["pos"], v["alt"]))
    return {
        "schema_version": 1,
        "id": f"mt-reviewed-variants-{release}",
        "name": "Reviewed mtDNA variants (ClinVar expert-panel pathogenic SNVs; "
        "CPIC MT-RNR1 aminoglycoside-risk alleles)",
        "reference": {"name": "rCRS", "accession": "NC_012920.1", "length": 16569},
        "sources": {
            "clinvar": {
                "release": file_date,
                "url": url,
                "md5": md5,
                "selection": "mtDNA SNVs; CLNSIG Pathogenic (or "
                "Pathogenic|drug_response); CLNREVSTAT "
                + " or ".join(REVIEWED)
                + "; no CLNSIGCONF",
                "terms": NCBI_TERMS,
                "attribution": "ClinVar, National Center for Biotechnology "
                "Information, U.S. National Library of Medicine",
            },
            "cpic": CPIC,
            "genes": "NC_012920.1 gene spans, NCBI feature table",
        },
        "variants": variants,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--release", required=True, help="ClinVar release date, e.g. 20261115"
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    table = build(args.release)
    args.output.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
    print(f"{args.output}: {len(table['variants'])} variants")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
