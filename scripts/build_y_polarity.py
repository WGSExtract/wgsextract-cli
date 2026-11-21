#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a separately licensed, offline YBrowse polarity correction module.

Run after explicitly downloading the pinned Yleaf tables and the YBrowse
snapshot published with the YFull module. No upstream trees/tables are changed
or republished. Only unambiguous, nonrecurrent single-base reversals with the
same SNP name AND position are corrected. Other disagreements are reported,
never guessed. Branch placement must match or be ancestral to the source node,
and corrected alleles must agree with the reference's known lineage.
Liftover must reproduce the upstream target position and allele
pair, including reverse-strand complementation.

    pixi run python scripts/build_y_polarity.py \
        --data out/reference/yleaf/v4.1.6 \
        --ybrowse out/ybrowse-snps_hg38-2026-11-05.csv.gz \
        --ref-hg38 hs38.fa.gz --ref-hg19 hg19.fa --ref-t2t hs1.fa \
        --output out/y-polarity --write-manifest
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pyliftover import LiftOver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_yfull_tree import chr_y, sequence_identity, sha256_file, write_pack
from wgsextract_cli.ydna.yleaf_native.markers import Marker, read_markers

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/wgsextract_cli/ydna/yleaf_native"
COMPLEMENT = str.maketrans("ACGT", "TGCA")
Site = tuple[int, str, str, str, str]


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
                sites[row["Name"]].add(
                    (
                        int(row["start"]),
                        anc,
                        der,
                        row["YCC_haplogroup"],
                        row["ISOGG_haplogroup"],
                    )
                )
    return dict(sites)


def ancestry(children: dict[str, list[str]]) -> dict[str, set[str]]:
    parents: dict[str, set[str]] = defaultdict(set)
    for parent, kids in children.items():
        for child in kids:
            parents[child].add(parent)
    return dict(parents)


def source_tree(
    tree: str, children: dict[str, list[str]], markers: list[Marker]
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Exclude FTDNA's marker-free legacy overlay, not ambiguous native edges."""
    selected = {node: list(kids) for node, kids in children.items()}
    stats: Counter[str] = Counter()
    if tree == "ftdna":
        branches = {marker.haplogroup for marker in markers}
        for child, parents in ancestry(children).items():
            native = parents & branches
            overlay = parents - native
            if (
                len(parents) > 1
                and len(native) == 1
                and "-" in next(iter(native))
                and all("-" not in node for node in overlay)
            ):
                for node in overlay:
                    selected[node].remove(child)
                    stats["legacy_overlay_edges_excluded"] += 1
    return selected, dict(stats)


def lineage(node: str, parents: dict[str, set[str]]) -> set[str]:
    """Require unique parentage; disconnected or cyclic paths are not evidence."""
    path: set[str] = set()
    while node:
        if node in path:
            raise ValueError("Cycle in haplogroup path")
        path.add(node)
        upstream = parents.get(node, set())
        if len(upstream) > 1:
            return set()
        node = next(iter(upstream), "")
    return path


def branch_aliases(
    children: dict[str, list[str]],
    markers: list[Marker],
    isogg: list[Marker],
) -> dict[str, set[str]]:
    """Translate named branches only through unique, shared marker placements."""
    parents = ancestry(children)
    active = set().union(
        *(lineage(node, parents) for node in {m.haplogroup for m in markers})
    )
    aliases: dict[str, set[str]] = defaultdict(set)
    names: dict[tuple[str, int, frozenset[str]], set[str]] = defaultdict(set)
    recurrent = Counter(marker.pos for marker in markers)
    isogg_recurrent = Counter(marker.pos for marker in isogg)
    for node in active:
        aliases[node].add(node)
        if "-" in node and any(c.isdigit() for c in node.split("-", 1)[1]):
            aliases[node.split("-", 1)[1]].add(node)
    for marker in markers:
        if recurrent[marker.pos] == 1:
            names[
                (
                    marker.name,
                    marker.pos,
                    frozenset((marker.ancestral, marker.derived)),
                )
            ].add(marker.haplogroup)
    for marker in isogg:
        if isogg_recurrent[marker.pos] == 1:
            aliases[marker.haplogroup].update(
                names.get(
                    (
                        marker.name,
                        marker.pos,
                        frozenset((marker.ancestral, marker.derived)),
                    ),
                    set(),
                )
            )
    return dict(aliases)


def audited_alleles(
    markers: list[Marker],
    sites: dict[str, set[Site]],
    lifter: LiftOver | None,
    children: dict[str, list[str]],
    aliases: dict[str, set[str]],
    sequence: str,
    reference_node: str,
) -> tuple[list[list[Any]], dict[str, int]]:
    parents = ancestry(children)
    reference_path = lineage(reference_node, parents)
    if reference_node not in children or not reference_path:
        raise ValueError(f"Unknown or ambiguous reference lineage: {reference_node}")
    descendants: set[str] = set()
    pending = list(children[reference_node])
    while pending:
        node = pending.pop()
        if node not in descendants:
            descendants.add(node)
            pending.extend(children.get(node, []))
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
        pos, anc, der, ycc_branch, isogg_branch = next(iter(found))
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
            path = lineage(marker.haplogroup, parents)
            placements = [
                aliases[key]
                for branch in (ycc_branch, isogg_branch)
                if re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?", branch)
                for key in (
                    branch,
                    branch.split("-", 1)[1]
                    if "-" in branch
                    and any(c.isdigit() for c in branch.split("-", 1)[1])
                    else branch,
                )
                if key in aliases
            ]
            if (
                not path & reference_path
                or not placements
                or any(len(nodes) != 1 or not nodes <= path for nodes in placements)
            ):
                stats["branch_disagreement_not_corrected"] += 1
                continue
            if marker.haplogroup in descendants:
                stats["unresolved_reference_subclade_not_corrected"] += 1
                continue
            base = sequence[pos - 1] if 0 < pos <= len(sequence) else "N"
            if base not in (anc, der):
                stats["reference_mismatch_not_corrected"] += 1
                continue
            on_path = marker.haplogroup in reference_path
            if base != (der if on_path else anc):
                stats[
                    "path_ref_ancestral_not_corrected"
                    if on_path
                    else "off_path_ref_derived_not_corrected"
                ] += 1
                continue
            stats["corrected"] += 1
            alleles.append([marker.name, pos, anc, der])
    return sorted(alleles, key=lambda row: (row[1], row[0])), dict(stats)


def build(
    data: Path, ybrowse: Path, output: Path, references: dict[str, Path]
) -> dict[str, Any]:
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
    identities: dict[str, Any] = {}
    tree_identities: dict[str, str] = {}
    for build in ("hg38", "hg19", "t2t"):
        contig, sequence = chr_y(references[build])
        identities[build] = sequence_identity(references[build], contig, sequence)
        reference_node = "J1" if build == "t2t" else "R1b"
        identities[build]["haplogroup"] = reference_node
        isogg = read_markers(data / build / f"isogg_positions_{build}.txt")
        for tree in ("ftdna", "isogg"):
            tree_path = data / "hg_prediction_tables" / f"{tree}_tree.json"
            children = json.loads(tree_path.read_text())
            tree_identities[tree] = sha256_file(tree_path)
            path = data / build / f"{tree}_positions_{build}.txt"
            markers = read_markers(path)
            children, tree_audit = source_tree(tree, children, markers)
            aliases = branch_aliases(children, markers, isogg)
            native_reference = (
                ("J-M267" if build == "t2t" else "R-M343")
                if tree == "ftdna"
                else reference_node
            )
            if native_reference not in children:
                raise ValueError(f"Unknown {tree} reference branch: {native_reference}")
            alleles, audit = audited_alleles(
                markers,
                sites,
                lifters.get(build),
                children,
                aliases,
                sequence,
                native_reference,
            )
            tables[f"{tree}/{build}"] = {
                "sha256": sha256_file(path),
                "alleles": alleles,
                "audit": audit,
                "tree_audit": tree_audit,
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
        "references": identities,
        "trees": tree_identities,
        "tables": tables,
    }
    content = (json.dumps(metadata, indent=2) + "\n").encode()
    notice = (
        "# YBrowse polarity attribution\n\n"
        "The SNP names, positions and corrected ancestral/derived alleles are\n"
        "adapted from YBrowse snps_hg38.csv (<http://ybrowse.org/gbrowse2/gff/>),\n"
        f"snapshot `{ybrowse.name}`, SHA-256 `{sha256_file(ybrowse)}`.\n"
        "CC-BY-SA Thomas Krahn YSEQ (2022); no licence version is stated.\n"
        "Changes: branch-compatible, reference-lineage-checked reversals, excluding\n"
        "recurrent positions, and lifted to hg19 and T2T with strand complementation.\n"
        "This separately downloaded module contains no FTDNA/ISOGG tree or table.\n"
    ).encode()
    info = {
        "source": metadata["source"],
        "liftover": metadata["liftover"],
        "references": identities,
        "trees": tree_identities,
        "tables": {
            key: {k: v for k, v in value.items() if k != "alleles"}
            for key, value in tables.items()
        },
        "script": "scripts/build_y_polarity.py",
    }
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "y-polarity-2.tar.gz"
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
        "url": "https://github.com/WGSExtract/wgsextract-cli/releases/download/yleaf-polarity-v2/y-polarity-2.tar.gz",
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
    for genome in ("hg38", "hg19", "t2t"):
        parser.add_argument(f"--ref-{genome}", type=Path, required=True)
    args = parser.parse_args()
    spec = build(
        args.data,
        args.ybrowse,
        args.output,
        {genome: getattr(args, f"ref_{genome}") for genome in ("hg38", "hg19", "t2t")},
    )
    if args.write_manifest:
        path = PACKAGE / "trees.json"
        manifest = json.loads(path.read_text())
        for tree in ("ftdna", "isogg"):
            manifest["trees"][tree]["modules"] = {"polarity": spec}
        path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
