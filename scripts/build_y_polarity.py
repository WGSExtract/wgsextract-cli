#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a separately licensed, offline YBrowse polarity correction module.

Run after explicitly downloading the pinned Yleaf tables and the YBrowse
snapshot published with the YFull module. No upstream trees/tables are changed
or republished. Only unambiguous, nonrecurrent single-base reversals with the
same SNP name AND position are corrected. Other disagreements are reported,
never guessed. Liftover must reproduce the upstream target position and allele
pair, including reverse-strand complementation.

    pixi run python scripts/build_y_polarity.py \
        --data out/reference/yleaf/v4.1.6 \
        --ybrowse out/ybrowse-snps_hg38-2026-11-05.csv.gz \
        --output out/y-polarity --write-manifest
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pyliftover import LiftOver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_yfull_tree import sha256_file, write_pack
from wgsextract_cli.ydna.yleaf_native.markers import Marker, read_markers

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/wgsextract_cli/ydna/yleaf_native"
COMPLEMENT = str.maketrans("ACGT", "TGCA")
Site = tuple[int, str, str]


def ybrowse_sites(path: Path) -> dict[str, set[Site]]:
    sites: dict[str, set[Site]] = defaultdict(set)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            anc, der = row["allele_anc"].upper(), row["allele_der"].upper()
            if (
                row["seqid"] == "chrY"
                and row["start"] == row["end"]
                and anc in {"A", "C", "G", "T"}
                and der in {"A", "C", "G", "T"}
                and anc != der
            ):
                sites[row["Name"]].add((int(row["start"]), anc, der))
    return dict(sites)


def audited_alleles(
    markers: list[Marker], sites: dict[str, set[Site]], lifter: LiftOver | None
) -> tuple[list[list[Any]], dict[str, int]]:
    recurrent = Counter(marker.pos for marker in markers)
    stats: Counter[str] = Counter()
    alleles: list[list[Any]] = []
    for marker in markers:
        found = sites.get(marker.name, set())
        if not found:
            stats["no_independent_snp"] += 1
            continue
        if len(found) != 1:
            stats["ambiguous_independent_snp"] += 1
            continue
        pos, anc, der = next(iter(found))
        if lifter is not None:
            hits = [
                hit
                for hit in (lifter.convert_coordinate("chrY", pos - 1) or [])
                if hit[0] == "chrY"
            ]
            if len(hits) != 1:
                stats["unlifted"] += 1
                continue
            _, target, strand, _ = hits[0]
            pos = int(target) + 1
            if strand == "-":
                anc, der = anc.translate(COMPLEMENT), der.translate(COMPLEMENT)
        if marker.pos != pos:
            stats["position_disagreement"] += 1
        elif (marker.ancestral, marker.derived) == (anc, der):
            stats["agrees"] += 1
        elif (marker.ancestral, marker.derived) != (der, anc):
            stats["allele_disagreement"] += 1
        elif recurrent[pos] != 1:
            stats["recurrent_reversal_not_corrected"] += 1
        else:
            stats["corrected"] += 1
            alleles.append([marker.name, pos, anc, der])
    return sorted(alleles, key=lambda row: (row[1], row[0])), dict(stats)


def build(data: Path, ybrowse: Path, output: Path) -> dict[str, Any]:
    sites = ybrowse_sites(ybrowse)
    tables: dict[str, Any] = {}
    chains = {
        build: data / build / name
        for build, name in (
            ("hg19", "hg38ToHg19.over.chain.gz"),
            ("t2t", "hg38ToHs1.over.chain.gz"),
        )
    }
    lifters = {build: LiftOver(str(chain)) for build, chain in chains.items()}
    for build in ("hg38", "hg19", "t2t"):
        for tree in ("ftdna", "isogg"):
            path = data / build / f"{tree}_positions_{build}.txt"
            markers = read_markers(path)
            alleles, audit = audited_alleles(markers, sites, lifters.get(build))
            tables[f"{tree}/{build}"] = {
                "sha256": sha256_file(path),
                "alleles": alleles,
                "audit": audit,
            }
            print(f"{tree}/{build}: {audit}")
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "source": {
            "name": "YBrowse",
            "snapshot": ybrowse.name,
            "sha256": sha256_file(ybrowse),
            "license": "CC-BY-SA (version not stated)",
            "copyright": "CC-BY-SA Thomas Krahn YSEQ (2022).",
        },
        "liftover": {build: sha256_file(chain) for build, chain in chains.items()},
        "tables": tables,
    }
    content = (json.dumps(metadata, indent=2) + "\n").encode()
    notice = (
        "# YBrowse polarity attribution\n\n"
        "The SNP names, positions and corrected ancestral/derived alleles are\n"
        "adapted from YBrowse snps_hg38.csv (<http://ybrowse.org/gbrowse2/gff/>),\n"
        f"snapshot `{ybrowse.name}`, SHA-256 `{sha256_file(ybrowse)}`.\n"
        "CC-BY-SA Thomas Krahn YSEQ (2022); no licence version is stated.\n"
        "Changes: selected unambiguous reversals in pinned Yleaf tables, excluding\n"
        "recurrent positions, and lifted to hg19 and T2T with strand complementation.\n"
        "This separately downloaded module contains no FTDNA/ISOGG tree or table.\n"
    ).encode()
    info = {
        "source": metadata["source"],
        "liftover": metadata["liftover"],
        "tables": {
            key: {k: v for k, v in value.items() if k != "alleles"}
            for key, value in tables.items()
        },
        "script": "scripts/build_y_polarity.py",
    }
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "y-polarity-1.tar.gz"
    files = write_pack(
        {
            "polarity.json": content,
            "build_info.json": (json.dumps(info, indent=2) + "\n").encode(),
            "NOTICE-YBrowse.md": notice,
        },
        archive,
    )
    (output / "build_info.json").write_text(json.dumps(info, indent=2) + "\n")
    spec = {
        "folder": "y-polarity",
        "license": metadata["source"]["license"],
        "source": f"YBrowse snapshot {ybrowse.name}",
        "notice": "Licence: Y marker polarity corrections are a separate CC-BY-SA YBrowse data module (NOTICE-YBrowse.md).",
        "url": "https://github.com/WGSExtract/wgsextract-cli/releases/download/yleaf-polarity-v1/y-polarity-1.tar.gz",
        "sha256": sha256_file(archive),
        "size": archive.stat().st_size,
        "files": files,
    }
    (output / "module.json").write_text(json.dumps(spec, indent=2) + "\n")
    return spec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--ybrowse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    spec = build(args.data, args.ybrowse, args.output)
    if args.write_manifest:
        path = PACKAGE / "trees.json"
        manifest = json.loads(path.read_text())
        for tree in ("ftdna", "isogg"):
            manifest["trees"][tree]["modules"] = {"polarity": spec}
        path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
