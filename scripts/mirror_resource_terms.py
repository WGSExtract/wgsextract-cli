"""Retained audit-time terms, with a bounded live-fetch fallback."""

from __future__ import annotations

import hashlib
import json
import logging
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from scripts.mirror_resource_transfer import USER_AGENT

SNAPSHOT_INDEX = (
    Path(__file__).resolve().parents[1]
    / "src/wgsextract_cli/assets/resource_terms/sources.json"
)


def raw_github_url(url: str) -> str:
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if parsed.netloc == "github.com" and len(parts) >= 5 and parts[2] == "blob":
        return f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/" + "/".join(
            parts[3:]
        )
    return url


def snapshot_record(url: str) -> dict[str, str] | None:
    sources = json.loads(SNAPSHOT_INDEX.read_text(encoding="utf-8"))["sources"]
    record = sources.get(url) or sources.get(raw_github_url(url))
    return dict(record) if record is not None else None


def saved_terms(url: str) -> bytes | None:
    record = snapshot_record(url)
    if record is None:
        return None
    content = (SNAPSHOT_INDEX.parent / record["file"]).read_bytes()
    if hashlib.sha256(content).hexdigest() != record["sha256"]:
        raise ValueError(f"Committed source-terms checksum mismatch: {url}")
    return content


def save_terms(url: str, path: Path, *, prefer_snapshot: bool = True) -> None:
    snapshot = saved_terms(url)
    if prefer_snapshot and snapshot is not None:
        path.write_bytes(snapshot)
        return
    try:
        request = Request(raw_github_url(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            content = response.read(2_000_001)
            expected = int(response.headers.get("Content-Length") or 0)
        if (
            not content
            or len(content) > 2_000_000
            or (expected and len(content) != expected)
        ):
            raise ValueError(f"Incomplete or oversized source terms: {url}")
    except (OSError, HTTPException, ValueError):
        if snapshot is None:
            raise
        logging.warning(
            "Source terms unavailable at %s; using the committed audit snapshot.", url
        )
        content = snapshot
    path.write_bytes(content)
