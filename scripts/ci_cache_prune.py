"""Reclaim superseded Actions caches without deleting current shared inputs."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scripts.ci_github import GitHub

MAIN = "refs/heads/main"
SHA = r"[0-9a-f]{40}"
DIGEST = r"[0-9a-f]{64}"
PIXI = re.compile(
    rf"(?P<group>pixi-v2-(?P<os>Linux|Windows|macOS)-(?:X64|ARM64)-"
    rf"v\d+\.\d+\.\d+-(?:cli|lint|optional-tools))-(?P<lock>{DIGEST})-(?P<workspace>{DIGEST})"
)
CODEQL = re.compile(
    rf"(?P<group>codeql-overlay-base-database-\d+-[0-9a-f]{{16}}-[a-z][a-z0-9-]*-"
    rf"\d+\.\d+\.\d+)-(?P<sha>{SHA})-\d+-\d+"
)
TEST_PASS = re.compile(
    r"(?P<group>test-pass-v1-(?:ubuntu|macos|windows|linux-arm64)-"
    r"[a-z0-9-]+-(?P<scope>main|pr))-\d+-\d+"
)
KEEP_OVERLAYS = 2
KEEP_TEST_PASSES = 3


@dataclass(frozen=True)
class CacheEntry:
    id: int
    key: str
    ref: str
    version: str
    bytes: int
    created: datetime


def _lock_hash(contents: bytes) -> str:
    # hashFiles hashes the concatenated raw digests of the matching files.
    return hashlib.sha256(hashlib.sha256(contents).digest()).hexdigest()


def superseded(entries: list[CacheEntry], *, lock: bytes, tip: str) -> list[CacheEntry]:
    """Keep unknown families and replace only caches in matching scopes."""
    lf = lock.replace(b"\r\n", b"\n")
    current = _lock_hash(lf)
    current_locks = {current, _lock_hash(lock), _lock_hash(lf.replace(b"\n", b"\r\n"))}
    pixi: dict[tuple[str, str, str], list[tuple[CacheEntry, bool]]] = defaultdict(list)
    overlays: dict[str, list[tuple[CacheEntry, bool]]] = defaultdict(list)
    test_passes: dict[str, list[tuple[CacheEntry, bool]]] = defaultdict(list)
    for entry in {item.id: item for item in entries}.values():
        if (match := TEST_PASS.fullmatch(entry.key)) and (
            (match["scope"] == "main" and entry.ref == MAIN)
            or (
                match["scope"] == "pr"
                and re.fullmatch(r"refs/pull/\d+/merge", entry.ref)
            )
        ):
            test_passes[f"{entry.ref}:{match['group']}"].append((entry, False))
        elif entry.ref != MAIN:
            continue
        elif entry.version and (match := PIXI.fullmatch(entry.key)):
            pixi[match["group"], match["workspace"], entry.version].append(
                (entry, match["lock"] in current_locks)
            )
        elif match := CODEQL.fullmatch(entry.key):
            overlays[match["group"]].append((entry, match["sha"] == tip))
    result = [
        entry
        for candidates in pixi.values()
        if any(is_current for _, is_current in candidates)
        for entry, is_current in candidates
        if not is_current
    ]
    for groups, keep in ((overlays, KEEP_OVERLAYS), (test_passes, KEEP_TEST_PASSES)):
        for candidates in groups.values():
            newest = sorted(
                candidates, key=lambda item: (item[0].created, item[0].id), reverse=True
            )
            result.extend(entry for entry, pinned in newest[keep:] if not pinned)
    return sorted(result, key=lambda entry: entry.id)


def _is_current(gh: GitHub, sha: str) -> bool:
    ref = gh.api("GET", "git/ref/heads/main")
    return bool(ref.get("ref") == MAIN and ref["object"]["sha"] == sha)


def _inventory(gh: GitHub) -> list[CacheEntry]:
    entries: dict[int, CacheEntry] = {}
    total = None
    for page in range(1, 101):
        response = gh.api(
            "GET",
            f"actions/caches?sort=created_at&direction=asc&per_page=100&page={page}",
        )
        if total is None:
            total = response["total_count"]
        rows = response["actions_caches"]
        if response["total_count"] != total:
            break
        for row in rows:
            item = CacheEntry(
                id=int(row["id"]),
                key=row["key"],
                ref=row["ref"],
                version=row["version"],
                bytes=int(row["size_in_bytes"]),
                created=datetime.fromisoformat(
                    row["created_at"].replace("Z", "+00:00")
                ),
            )
            if item.id in entries:
                raise ValueError("Cache inventory repeated an ID; refusing cleanup")
            entries[item.id] = item
        if len(entries) == total:
            return list(entries.values())
        if len(rows) < 100:
            break
    raise ValueError(
        "Cache inventory changed or pagination was incomplete; refusing cleanup"
    )


def prune(gh: GitHub, *, sha: str, lock: bytes, apply: bool) -> str:
    before = gh.api("GET", "actions/cache/usage")["active_caches_size_in_bytes"]
    entries = _inventory(gh)
    planned = superseded(entries, lock=lock, tip=sha)
    lines = [
        f"Cache usage before: {before:,} bytes",
        f"{len(planned)} superseded entries: {sum(item.bytes for item in planned):,} bytes",
    ]
    if not apply:
        lines += [
            f"- Would delete `{item.key}` ({item.bytes:,} bytes)." for item in planned
        ]
        return "\n".join(lines)
    for item in planned:
        if not _is_current(gh, sha):
            lines.append("Main moved; stopped before deleting any more entries.")
            break
        gh.api("DELETE", f"actions/caches/{item.id}")
        lines.append(f"- Deleted `{item.key}` ({item.bytes:,} bytes).")
    after = gh.api("GET", "actions/cache/usage")["active_caches_size_in_bytes"]
    lines.append(f"Cache usage after: {after:,} bytes (the API total can lag).")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument(
        "--sha", required=True, help="Full SHA of the checked out main tip"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Delete, rather than only list"
    )
    parser.add_argument(
        "--summary", type=Path, help="Write the result to this step summary"
    )
    args = parser.parse_args()
    if not re.fullmatch(SHA, args.sha):
        parser.error("--sha must be a full Git SHA")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if head != args.sha:
        parser.error("--sha must match the checkout")
    # Read the committed lock; worktree edits or line ending conversion cannot
    # turn a current generation into a deletion candidate.
    lock = subprocess.check_output(["git", "show", f"{head}:pixi.lock"])
    report = prune(GitHub(args.repository), sha=head, lock=lock, apply=args.apply)
    print(report)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as file:
            file.write(f"### Actions cache maintenance\n\n{report}\n")


if __name__ == "__main__":
    main()
