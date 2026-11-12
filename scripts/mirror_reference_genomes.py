#!/usr/bin/env python3
"""Mirror the reference-genome catalog onto this repository's GitHub releases.

The catalog (seed_genomes.csv) lists each genome's sources in the order
``ref library --install`` tries them. A mirrored genome's first row points at
its copy here, pinned by SHA-256, and its original source follows as an -Alt
row (#701). The mirror workflow (.github/workflows/mirror_reference_genomes.yml)
runs this script:

  plan [--only FINAL,...] [--skip-uploaded TAG]
      JSON list of {"final", "code"} for the catalog's genomes with no GitHub
      copy yet (or just those named), less those NOT_MIRRORED and those
      already uploaded to TAG.
  build FINAL --out DIR
      Install FINAL from its original sources, exactly as a user's install
      does (BGZF recompression, sequence-dictionary signature check), into
      DIR/FINAL, and write DIR/FINAL.json: its source, size and SHA-256.
  notes RECORD.json...
      The release notes: each file, where it came from, its terms and its
      SHA-256.
  catalog TAG RECORD.json...
      Rewrite seed_genomes.csv: each mirrored genome's GitHub row takes its
      first row's place, and its original source follows as an -Alt row (in
      the Advanced block for a genome the GUI shows by default). GitHub rows
      already there are pinned to GitHub's SHA-256 digests.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

GITHUB = "GitHub"
REPO = "WGSExtract/wgsextract-cli"
CATALOG = (
    Path(__file__).resolve().parents[1]
    / "src/wgsextract_cli/assets/reference/seed_genomes.csv"
)
# The GUI shows the catalog rows up to this one; later rows are Advanced only
# (gui/examples/WGSExtract/scripts/shared/list_reference_genomes.py).
LAST_BASIC_CODE = "THGySeqp"
# GitHub's limit on one release asset is 2 GiB.
MAX_ASSET_BYTES = 2 * 1024**3 - 1
# Genomes not mirrored: their terms don't clearly allow redistribution (#701).
NOT_MIRRORED = {
    "hg01243_v3.fna.gz": "JHU's PR1 assembly states no licence",
    "hg19_yseq.fa.gz": "YSEQ publishes no terms for its downloads",
    "hg38_CP086569.fasta.gz": "YSEQ publishes no terms for its downloads",
}
# Each original host's terms, as docs/reference-genomes.md cites them.
TERMS = {
    "AWS": "T2T: public domain (CC0); HPRC: public domain",
    "NIH": "NCBI: no restrictions on use or distribution",
    "NCBI": "NCBI: no restrictions on use or distribution",
    "UCSC": "UCSC bigZips: freely available for public use",
    "EBI": "Ensembl / GENCODE: available without restriction",
}


def _upstream(final: str) -> list[dict[str, Any]]:
    """FINAL's original sources, in catalog order."""
    from wgsextract_cli.core.ref_library import get_available_genomes

    return [
        g
        for g in get_available_genomes()
        if g["final"] == final and g["source"] != GITHUB
    ]


def plan(only: list[str], uploaded: set[str]) -> list[dict[str, str]]:
    from wgsextract_cli.core.ref_library import get_available_genomes

    genomes = get_available_genomes()
    mirrored = {g["final"] for g in genomes if g["source"] == GITHUB}
    if not mirrored:
        # Without seed_genomes.csv the package falls back to a short built-in list.
        raise SystemExit(
            "The catalog (seed_genomes.csv) wasn't found; install with -e."
        )
    seen: set[str] = set()
    # A file two catalog names share (HPPv1 is HPPv11's) is mirrored once.
    urls: set[str] = set()
    todo = []
    for g in genomes:
        final = g["final"]
        if final in seen or (only and final not in only):
            continue
        seen.add(final)
        if final in mirrored or final in NOT_MIRRORED or final in uploaded:
            continue
        url = _upstream(final)[0]["url"].strip()
        if url in urls:
            continue
        urls.add(url)
        todo.append({"final": final, "code": g["code"]})
    unknown = set(only) - seen
    if unknown:
        raise SystemExit(f"Not in the catalog: {', '.join(sorted(unknown))}")
    return todo


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(final: str, out: Path) -> dict[str, Any]:
    from wgsextract_cli.core.reference_assets import reference_genomes_dir
    from wgsextract_cli.core.reference_processing import download_and_process_genome

    sources = _upstream(final)
    if not sources:
        raise SystemExit(f"{final} has no original source in the catalog.")
    library = out / "library"
    used = None
    for i, genome in enumerate(sources):
        if download_and_process_genome(genome, str(library), restart=i > 0):
            used = genome
            break
    if used is None:
        raise SystemExit(f"Could not install {final} from any of its sources.")
    installed = Path(reference_genomes_dir(str(library)), final)
    size = installed.stat().st_size
    if size > MAX_ASSET_BYTES:
        raise SystemExit(f"{final} is {size:,} bytes, over GitHub's 2 GiB limit.")
    target = out / final
    shutil.move(installed, target)
    shutil.rmtree(library)
    record = {
        "final": final,
        "code": used["code"],
        "label": used["label"],
        "source": used["source"],
        "url": used["url"],
        "signature": used["md5"],
        "size": size,
        "sha256": _sha256(target),
    }
    Path(f"{target}.json").write_text(
        json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    return record


def notes(records: list[dict[str, Any]]) -> str:
    lines = [
        "(AI): Copies of the reference genomes in WGS Extract's catalog "
        "(`seed_genomes.csv`), which `ref library --install` downloads first; the "
        "original source is the fallback (#701). Each file is the original FASTA "
        "recompressed to BGZF, with the same sequences: its sequence-dictionary "
        "signature matches the catalog's, which also pins its SHA-256. "
        "docs/reference-genomes.md describes the sources and their terms.",
        "",
        "| File | Genome | Original source | Terms | Size | SHA-256 |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for r in sorted(records, key=lambda r: r["final"]):
        host = r["source"].removesuffix("-Alt")
        lines.append(
            f"| {r['final']} | {r['label']} | {r['url']} | {TERMS.get(host, host)} "
            f"| {r['size'] / 1e9:.2f} GB | `{r['sha256']}` |"
        )
    return "\n".join(lines) + "\n"


def catalog(tag: str, records: list[dict[str, Any]]) -> str:
    """seed_genomes.csv with the mirrored genomes' GitHub rows."""
    from wgsextract_cli.core.ref_library.downloads import (
        resolve_github_release_asset_sha256,
    )

    with CATALOG.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if "SHA256" not in fields:
        fields.append("SHA256")
    by_url = {r["url"].strip(): r for r in records}
    by_final = {r["final"]: r for r in records}
    # The basic genomes' original sources join the Advanced block's -Alt rows.
    last_alt = max(i for i, r in enumerate(rows) if r["Source"].endswith("-Alt"))
    basic = True
    anchor = 0
    out: list[dict[str, str]] = []
    moved: list[dict[str, str]] = []
    # Genomes with a GitHub row already keep it (rerunning changes nothing).
    done = {r["Final File Name"] for r in rows if r["Source"] == GITHUB}
    for i, row in enumerate(rows):
        final = row["Final File Name"]
        record = by_final.get(final) or by_url.get(row["URL"].strip())
        if row["Source"] == GITHUB:
            if not row.get("SHA256"):
                row["SHA256"] = resolve_github_release_asset_sha256(row["URL"]) or ""
            out.append(row)
        elif record and final not in done:
            done.add(final)
            url = f"https://github.com/{REPO}/releases/download/{tag}/{record['final']}"
            out.append(row | {"Source": GITHUB, "URL": url, "SHA256": record["sha256"]})
            alt = row | {"Source": _alt(row["Source"]), "SHA256": ""}
            (moved if basic else out).append(alt)
        elif record:
            out.append(row | {"Source": _alt(row["Source"])})
        else:
            out.append(row)
        if row["Pyth Code"] == LAST_BASIC_CODE:
            basic = False
        if i == last_alt:
            anchor = len(out)
    out[anchor:anchor] = moved
    text = io.StringIO()
    writer = csv.DictWriter(text, fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(out)
    return text.getvalue()


def _alt(source: str) -> str:
    return source if source.endswith("-Alt") else f"{source}-Alt"


def _uploaded(tag: str) -> set[str]:
    listed = subprocess.run(
        ["gh", "release", "view", tag, "--repo", REPO, "--json", "assets",
         "--jq", ".assets[].name"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    return set(listed.stdout.split()) if listed.returncode == 0 else set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--only", default="", help="Comma-separated final file names")
    p.add_argument("--skip-uploaded", default="", metavar="TAG")
    b = sub.add_parser("build")
    b.add_argument("final")
    b.add_argument("--out", type=Path, required=True)
    n = sub.add_parser("notes")
    n.add_argument("records", nargs="+", type=Path)
    c = sub.add_parser("catalog")
    c.add_argument("tag")
    c.add_argument("records", nargs="+", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.cmd == "plan":
        only = [f.strip() for f in args.only.split(",") if f.strip()]
        uploaded = _uploaded(args.skip_uploaded) if args.skip_uploaded else set()
        print(json.dumps(plan(only, uploaded)))
    elif args.cmd == "build":
        args.out.mkdir(parents=True, exist_ok=True)
        print(json.dumps(build(args.final, args.out)))
    else:
        records = [json.loads(p.read_text(encoding="utf-8")) for p in args.records]
        if args.cmd == "notes":
            sys.stdout.write(notes(records))
        else:
            CATALOG.write_text(catalog(args.tag, records), encoding="utf-8", newline="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
