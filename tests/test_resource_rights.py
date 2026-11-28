"""Issue 864: released resource catalogs retain source-rights records."""

import csv
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/wgsextract_cli"
ASSETS = PACKAGE / "assets"


def test_864_missing_resource_rights_records() -> None:
    rights = json.loads((ASSETS / "resource_terms/rights.json").read_text())
    covered: dict[str, set[str]] = {}
    for row in rights["records"]:
        label = f"{row['catalog']}: {row['entries']}"
        for field in (
            "source_url",
            "license",
            "license_url",
            "attribution",
            "restrictions",
        ):
            assert row[field].strip(), f"{label} lacks {field}"
        for field in ("source_url", "license_url"):
            url = urlsplit(row[field])
            assert url.scheme == "https" and url.netloc, f"{label}: invalid {field}"
        if "unspecified" in row["license"].lower():
            assert row.get("reason", "").strip(), f"{label} lacks an uncertainty reason"
        entries = set(row["entries"])
        assert entries, f"{label} has no catalog entries"
        seen = covered.setdefault(row["catalog"], set())
        assert not seen & entries, f"{label} has conflicting rights records"
        seen.update(entries)

    yleaf = PACKAGE / "ydna/yleaf_native"
    trees = json.loads((yleaf / "trees.json").read_text())["trees"]
    ont = json.loads((ROOT / "docs/data/ont_hg002_manifest.json").read_text())
    with (ASSETS / "reference/seed_genomes.csv").open(
        newline="", encoding="utf-8"
    ) as file:
        genomes = {row["Pyth Code"] for row in csv.DictReader(file)}
    for catalog, entries in {
        "reference": genomes,
        "clair3": set(
            json.loads((ASSETS / "clair3/models.json").read_text())["models"]
        ),
        "yleaf-archive": set(
            json.loads((yleaf / "data_manifest.json").read_text())["archives"]
        ),
        "yleaf-tree": {
            entry
            for name, tree in trees.items()
            for entry in (f"{name}/{module}" for module in tree.get("modules", {}))
        }
        | {name for name, tree in trees.items() if tree["data"] == "yleaf"},
        "report-gene-list": set(
            json.loads((ASSETS / "report/gene_lists.json").read_text())["lists"]
        ),
        "ont-hg002": {
            ont["source"]["name"],
            Path(urlsplit(ont["source"]["index"]).path).name,
            *(item["name"] for item in ont["assets"] + ont["truth"]),
        },
    }.items():
        assert not entries - covered[catalog], (
            f"{catalog}: missing rights for {entries - covered[catalog]}"
        )

    mirrors = json.loads((ASSETS / "resource_mirrors.json").read_text())
    for row in mirrors["resources"]:
        for field in (
            "upstream_url",
            "licence",
            "licence_url",
            "attribution",
            "reason",
        ):
            assert row[field].strip(), f"{row['id']} lacks {field}"
