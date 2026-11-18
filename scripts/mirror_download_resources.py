#!/usr/bin/env python3
"""Publish audited, byte-identical download resources to immutable data releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.mirror_resource_terms import save_terms, snapshot_record
from scripts.mirror_resource_transfer import transfer_resource
from wgsextract_cli.core.resource_mirrors import MANIFEST, MAX_ASSET_BYTES, resources

REPO = "WGSExtract/wgsextract-cli"
RELEASE_ROOT = f"https://github.com/{REPO}/releases/download"
# Readable source-terms snapshots, rather than a JavaScript-only landing page.
LICENCE_SNAPSHOTS = {
    "CC0-1.0": "https://raw.githubusercontent.com/broadinstitute/gnomad-browser/"
    "c65e857d580c9ba3a2a1a64eecb565e282bce8e3/browser/about/policies/terms.md",
    "CC-BY-4.0": "https://raw.githubusercontent.com/google-deepmind/alphamissense/"
    "fe2dc845f93310abd6c1b0e8955d7a96c2144d66/README.md",
}


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args, "--repo", REPO], text=True)


def validate_tag(tag: str) -> str:
    if not re.fullmatch(r"download-resources-[a-zA-Z0-9._-]+", tag):
        raise ValueError("Tag must start with download-resources- and be a safe tag.")
    return tag


def validate_record(record: dict[str, Any], entry: dict[str, Any], tag: str) -> None:
    if not entry["redistributable"]:
        raise ValueError(f"{entry['id']} is upstream-only: {entry['reason']}")
    for key in (
        "id",
        "upstream_url",
        "version",
        "licence",
        "licence_url",
        "attribution",
    ):
        if record.get(key) != entry[key]:
            raise ValueError(f"{entry['id']}: record changed audited {key}")
    if not record.get("retrieved_at") or not record.get("parts"):
        raise ValueError(f"{entry['id']}: missing retrieval date or parts")
    if not re.fullmatch(r"[a-f0-9]{64}", str(record.get("sha256"))):
        raise ValueError("Invalid resource SHA-256")
    total = 0
    for index, part in enumerate(record["parts"]):
        expected = f"{RELEASE_ROOT}/{tag}/{entry['id']}.part{index:04d}"
        if part["url"] != expected:
            raise ValueError(f"Unexpected part URL: {part['url']}")
        if not re.fullmatch(r"[a-f0-9]{64}", str(part["sha256"])):
            raise ValueError("Invalid part SHA-256")
        if not 0 < part["size"] <= MAX_ASSET_BYTES:
            raise ValueError("Part exceeds the release-asset size limit")
        total += part["size"]
    if total != record["size"]:
        raise ValueError("Parts do not sum to resource size")


def release_assets(tag: str) -> dict[str, dict[str, Any]]:
    data = json.loads(gh("release", "view", tag, "--json", "assets"))
    return {asset["name"]: asset for asset in data["assets"]}


def upload(
    path: Path,
    tag: str,
    existing: dict[str, dict[str, Any]],
    *,
    expected_sha256: str | None = None,
) -> None:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            checksum.update(chunk)
    digest = checksum.hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError(f"Staged part checksum changed: {path.name}")
    asset = existing.get(path.name)
    if not asset:
        gh("release", "upload", tag, str(path))
        asset = release_assets(tag).get(path.name, {})
    if (
        asset.get("digest") != f"sha256:{digest}"
        or asset.get("size") != path.stat().st_size
    ):
        raise ValueError(
            f"{path.name} release checksum/size differs; never overwrite it, use a new tag"
        )
    existing[path.name] = asset


def publish(entry: dict[str, Any], tag: str, out: Path) -> dict[str, Any]:
    validate_tag(tag)
    if not entry["redistributable"]:
        raise ValueError(f"{entry['id']} is upstream-only: {entry['reason']}")
    out.mkdir(parents=True, exist_ok=True)
    existing = release_assets(tag)
    record_name = f"{entry['id']}.record.json"
    if record_name in existing:
        gh(
            "release",
            "download",
            tag,
            "--pattern",
            record_name,
            "--dir",
            str(out),
            "--clobber",
        )
        record: dict[str, Any] = json.loads((out / record_name).read_text())
        validate_record(record, entry, tag)
        for part in record["parts"]:
            name = part["url"].rsplit("/", 1)[-1]
            asset = existing.get(name, {})
            if (
                asset.get("digest") != f"sha256:{part['sha256']}"
                or asset.get("size") != part["size"]
            ):
                raise ValueError(f"Published part {name} no longer matches its record")
        return record

    record = dict(entry, retrieved_at=datetime.now(timezone.utc).isoformat(), parts=[])
    record["licence_snapshot_url"] = LICENCE_SNAPSHOTS.get(
        entry["licence"], entry["licence_url"]
    )
    # Fail before transferring tens of GB if the retained terms are unavailable.
    licence = out / f"{entry['id']}.LICENCE.txt"
    save_terms(record["licence_snapshot_url"], licence)
    record["licence_snapshot_provenance"] = snapshot_record(
        record["licence_snapshot_url"]
    )
    upload(licence, tag, existing)
    if entry["licence"] in {"CC-BY-4.0", "CC0-1.0"}:
        legal_url = (
            "https://creativecommons.org/licenses/by/4.0/legalcode.txt"
            if entry["licence"] == "CC-BY-4.0"
            else "https://creativecommons.org/publicdomain/zero/1.0/legalcode.txt"
        )
        legal = out / f"{entry['id']}.LEGALCODE.txt"
        save_terms(legal_url, legal)
        record["legalcode_snapshot_provenance"] = snapshot_record(legal_url)
        upload(legal, tag, existing)
    total = 0

    def publish_part(path: Path, digest: str, size: int) -> None:
        nonlocal total
        upload(path, tag, existing, expected_sha256=digest)
        total += size
        record["parts"].append(
            {"url": f"{RELEASE_ROOT}/{tag}/{path.name}", "size": size, "sha256": digest}
        )
        print(f"{entry['id']}: published {total:,} bytes", flush=True)

    record.update(
        transfer_resource(
            entry["upstream_url"],
            out,
            entry["id"],
            MAX_ASSET_BYTES,
            publish_part,
            expected_sha256=None
            if entry.get("upstream_mutable")
            else entry.get("sha256"),
        )
    )
    validate_record(record, entry, tag)

    notice = out / f"{entry['id']}.NOTICE.txt"
    notice.write_text(
        f"{entry['attribution']}\n\nUnmodified bytes from {entry['upstream_url']}\n"
        f"Version: {entry['version']}\nRetrieved: {record['retrieved_at']}\n"
        f"Licence: {entry['licence']}\nSource terms: {entry['licence_url']}\n"
        f"SHA-256: {record['sha256']}\nSize: {total}\n\n"
        f"Source licence caveats: {entry['reason']}\n",
        encoding="utf-8",
    )
    upload(notice, tag, existing)
    # Publish the commit record LAST: absence means the resource is incomplete.
    path = out / record_name
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    upload(path, tag, existing)
    return record


def activate(records: Path, tag: str) -> None:
    validate_tag(tag)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    by_id = {entry["id"]: entry for entry in manifest["resources"]}
    paths = sorted(records.glob("*.record.json"))
    if not paths:
        raise ValueError("No *.record.json publication records found")
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        entry = by_id[record["id"]]
        validate_record(record, entry, tag)
        entry.update(
            {key: record[key] for key in ("parts", "size", "sha256", "retrieved_at")}
        )
        entry["upstream_last_modified"] = record.get("upstream_last_modified")
        entry["licence_snapshot_url"] = record.get(
            "licence_snapshot_url", entry["licence_url"]
        )
    manifest["release_tag"] = tag
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--only", default="")
    build = sub.add_parser("publish")
    build.add_argument("resource")
    build.add_argument("--tag", required=True)
    build.add_argument("--out", type=Path, required=True)
    pins = sub.add_parser("activate")
    pins.add_argument("--records", type=Path, required=True)
    pins.add_argument("--tag", required=True)
    args = parser.parse_args()
    by_id = {entry["id"]: entry for entry in resources().values()}
    if args.command == "plan":
        ids = (
            args.only.split(",")
            if args.only
            else [key for key, entry in by_id.items() if entry["redistributable"]]
        )
        for key in ids:
            if key not in by_id or not by_id[key]["redistributable"]:
                raise ValueError(f"Not approved for redistribution: {key}")
        print(json.dumps(ids))
    elif args.command == "publish":
        publish(by_id[args.resource], args.tag, args.out)
    else:
        activate(args.records, args.tag)


if __name__ == "__main__":
    main()
