#!/usr/bin/env python3
"""Publish audited, byte-identical download resources to immutable data releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

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


def upload(path: Path, tag: str, existing: dict[str, dict[str, Any]]) -> None:
    if asset := existing.get(path.name):
        checksum = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1 << 20), b""):
                checksum.update(chunk)
        digest = checksum.hexdigest()
        if (
            asset.get("digest") != f"sha256:{digest}"
            or asset["size"] != path.stat().st_size
        ):
            raise ValueError(
                f"{path.name} already exists with different bytes; use a new tag"
            )
        return
    gh("release", "upload", tag, str(path))


def save_terms(url: str, path: Path) -> None:
    with urlopen(url, timeout=60) as response:
        content = response.read(2_000_001)
        expected = int(response.headers.get("Content-Length") or 0)
    if (
        not content
        or len(content) > 2_000_000
        or (expected and len(content) != expected)
    ):
        raise ValueError(f"Incomplete or oversized source terms: {url}")
    path.write_bytes(content)


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
    upload(licence, tag, existing)
    if entry["licence"] in {"CC-BY-4.0", "CC0-1.0"}:
        legal_url = (
            "https://creativecommons.org/licenses/by/4.0/legalcode.txt"
            if entry["licence"] == "CC-BY-4.0"
            else "https://creativecommons.org/publicdomain/zero/1.0/legalcode.txt"
        )
        legal = out / f"{entry['id']}.LEGALCODE.txt"
        save_terms(legal_url, legal)
        upload(legal, tag, existing)
    whole = hashlib.sha256()
    total = 0
    # Keep one <=1.9 GB part on disk, even for the 72 GB gnomAD contigs.
    request = Request(
        entry["upstream_url"], headers={"User-Agent": "wgsextract-cli-mirror"}
    )
    with urlopen(request, timeout=120) as response:
        expected = int(response.headers.get("Content-Length") or 0)
        last_modified = response.headers.get("Last-Modified")
        record["upstream_last_modified"] = last_modified
        while True:
            index = len(record["parts"])
            path = out / f"{entry['id']}.part{index:04d}"
            digest = hashlib.sha256()
            size = 0
            try:
                with path.open("wb") as target:
                    while size < MAX_ASSET_BYTES:
                        chunk = response.read(min(1 << 20, MAX_ASSET_BYTES - size))
                        if not chunk:
                            break
                        target.write(chunk)
                        size += len(chunk)
                        total += len(chunk)
                        digest.update(chunk)
                        whole.update(chunk)
                if size == 0:
                    break
                if size < MAX_ASSET_BYTES and expected and total != expected:
                    raise ValueError(
                        f"Incomplete upstream response: {total} of {expected} bytes"
                    )
                upload(path, tag, existing)
                record["parts"].append(
                    {
                        "url": f"{RELEASE_ROOT}/{tag}/{path.name}",
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
                )
                print(f"{entry['id']}: published {total:,} bytes", flush=True)
            finally:
                path.unlink(missing_ok=True)
        if total == 0 or (expected and total != expected):
            raise ValueError(
                f"Incomplete upstream response: {total} of {expected} bytes"
            )
    record["size"] = total
    record["sha256"] = whole.hexdigest()
    validate_record(record, entry, tag)

    notice = out / f"{entry['id']}.NOTICE.txt"
    notice.write_text(
        f"{entry['attribution']}\n\nUnmodified bytes from {entry['upstream_url']}\n"
        f"Version: {entry['version']}\nRetrieved: {record['retrieved_at']}\n"
        f"Licence: {entry['licence']}\nSource terms: {entry['licence_url']}\n"
        f"SHA-256: {record['sha256']}\nSize: {total}\n\n{entry['reason']}\n",
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
