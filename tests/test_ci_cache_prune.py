"""Cache hygiene acts only on known superseded main caches."""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from scripts.ci_cache_prune import CacheEntry, prune, superseded
from scripts.ci_github import GitHub

TIP = "e" * 40
LOCK = b"lock-file\n"
MAIN = "refs/heads/main"


def inventory() -> list[CacheEntry]:
    def fingerprint(contents: bytes) -> str:
        return hashlib.sha256(hashlib.sha256(contents).digest()).hexdigest()

    lf = fingerprint(LOCK)
    crlf = fingerprint(LOCK.replace(b"\n", b"\r\n"))
    old = "a" * 64
    workspace = "b" * 64
    other_workspace = "c" * 64
    linux = "pixi-v2-Linux-X64-v0.82.0-cli-"
    windows = "pixi-v2-Windows-X64-v0.82.0-cli-"
    overlay = "codeql-overlay-base-database-1-a84155eaf49db831-python-2.27.5-"
    test = "test-pass-v1-ubuntu-all-main-"
    now = datetime.now(timezone.utc)
    keys = [
        (f"{linux}{lf}-{workspace}", MAIN),
        (f"{linux}{old}-{workspace}", MAIN),  # superseded
        (f"{linux}{old}-{other_workspace}", MAIN),  # no replacement
        (f"{windows}{crlf}-{workspace}", MAIN),
        (f"{windows}{lf}-{workspace}", MAIN),
        (f"{windows}{old}-{workspace}", MAIN),  # superseded
        (f"{linux}{old}-{workspace}", "refs/pull/42/merge"),
        (f"{overlay}{'1' * 40}-5000000000-1", MAIN),
        (f"{overlay}{'2' * 40}-4900000000-1", MAIN),
        (f"{overlay}{'3' * 40}-4800000000-1", MAIN),  # superseded
        (f"{overlay}{TIP}-4700000000-1", MAIN),  # current main regardless of age
        (f"{overlay.replace('python', 'javascript')}{'4' * 40}-4600000000-1", MAIN),
        *[(f"{test}{run}-1", MAIN) for run in range(5000000000, 4999999996, -1)],
        ("test-pass-v1-ubuntu-all-pr-4999999991-1", "refs/pull/42/merge"),
        ("hs38-v1-" + "d" * 64, MAIN),
        ("pixi-v2-Linux-X64-v0.82.0-unknown-builder-" + old + "-" + workspace, MAIN),
        *[
            (f"test-pass-v1-ubuntu-all-pr-{run}-1", "refs/pull/42/merge")
            for run in range(4999999990, 4999999987, -1)
        ],
        ("test-pass-v1-ubuntu-all-pr-4999999988-1", "refs/pull/43/merge"),
        (f"{linux}{'e' * 64}-{workspace}", MAIN),  # incompatible cache archive version
        (f"{linux}{crlf}-{workspace}", MAIN),  # equivalent checkout line endings
    ]
    return [
        CacheEntry(
            idx,
            key,
            ref,
            "v2" if idx == 24 else "v1",
            1000,
            now - timedelta(minutes=idx),
        )
        for idx, (key, ref) in enumerate(keys, 1)
    ]


@pytest.mark.parametrize("lock", [LOCK, LOCK.replace(b"\n", b"\r\n")])
def test_1502_only_superseded_main_cache_generations_are_eligible(lock: bytes) -> None:
    entries = inventory()
    assert [
        row.id for row in superseded([*entries, entries[12]], lock=lock, tip=TIP)
    ] == [
        2,
        6,
        10,
        16,
        22,
    ]


class CacheServer(GitHub):
    def __init__(self) -> None:
        super().__init__("owner/repo")
        self.entries = inventory()

    def request(self, method: str, path: str, data: object = None) -> Any:
        if method == "DELETE":
            deleted = int(path.rsplit("/", 1)[-1])
            self.entries = [row for row in self.entries if row.id != deleted]
            return None
        if path.endswith("actions/cache/usage"):
            return {
                "active_caches_size_in_bytes": sum(row.bytes for row in self.entries)
            }
        if path.endswith("git/ref/heads/main"):
            # Main advances immediately after the first deletion.
            sha = TIP if any(row.id == 2 for row in self.entries) else "f" * 40
            return {"ref": MAIN, "object": {"sha": sha}}
        if "/actions/caches?" in path:
            return {
                "total_count": len(self.entries),
                "actions_caches": [
                    {
                        "id": row.id,
                        "key": row.key,
                        "ref": row.ref,
                        "size_in_bytes": row.bytes,
                        "created_at": row.created.isoformat().replace("+00:00", "Z"),
                        "version": row.version,
                    }
                    for row in self.entries
                ],
            }
        raise AssertionError(path)


def test_1502_dry_run_does_not_delete_and_apply_stops_if_main_moves() -> None:
    server = CacheServer()
    before = len(server.entries)
    preview = prune(server, sha=TIP, lock=LOCK, apply=False)
    assert len(server.entries) == before
    assert "5 superseded entries: 5,000 bytes" in preview
    report = prune(server, sha=TIP, lock=LOCK, apply=True)
    assert [entry.id for entry in server.entries] == [
        entry.id for entry in inventory() if entry.id != 2
    ]
    assert "Main moved" in report
    assert f"Cache usage after: {(before - 1) * 1000:,} bytes" in report


def test_1502_cleanup_refuses_a_repeated_cache_id_before_deleting() -> None:
    server = CacheServer()
    server.entries.append(server.entries[0])
    with pytest.raises(ValueError, match="repeated an ID"):
        prune(server, sha=TIP, lock=LOCK, apply=True)
    assert server.entries[1].id == 2
