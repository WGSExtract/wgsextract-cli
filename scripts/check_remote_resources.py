#!/usr/bin/env python3
"""Probe only changed URLs on PRs; check all sources and mirror pins nightly."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import timezone
from email.utils import parsedate_to_datetime
from fnmatch import fnmatchcase
from functools import cache
from http.client import IncompleteRead, RemoteDisconnected
from pathlib import Path
from ssl import SSLEOFError
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MAX_RETRY_AFTER = 120


def snapshot(root: Path) -> dict[str, dict[str, Any]]:
    """Resolve tables in an isolated interpreter at each Git revision.

    Using the base's real tables also detects composed URL/version changes,
    unlike grepping added lines. No download functions are called.
    """
    source = """
import json, runpy, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(root), str(root / "src")]
module = runpy.run_path(str(root / "tests/test_remote_downloads.py"))
urls = module["_download_urls"]()
result = {url: {} for url in urls}
# Normalize the older inventory too: adding nightly coverage of an existing
# fallback host/contig is not a PR change to that download URL.
if "get_available_genomes" in module:
    catalogs = (module["get_available_genomes"](),
                module["FALLBACK_GENOMES"] + module["PET_GENOMES"])
    for catalog in catalogs:
        for genome in catalog:
            result.setdefault(genome["url"].strip(), {})
if "gnomad_af" in module:
    af = module["gnomad_af"]
    for contig in af.GNOMAD_V4_CONTIGS:
        result.setdefault(af.GNOMAD_V4_URL.format(contig=contig), {})
manifest = root / "src/wgsextract_cli/assets/resource_mirrors.json"
if manifest.exists():
    for entry in json.loads(manifest.read_text())["resources"]:
        result.setdefault(entry["upstream_url"], {})
        if entry["redistributable"]:
            for part in entry["parts"]:
                result[part["url"]] = {"sha256": part["sha256"], "size": part["size"]}
print(json.dumps(result))
"""
    return dict(
        json.loads(
            subprocess.check_output(
                [sys.executable, "-c", source, str(root)],
                cwd=root,
                text=True,
            )
        )
    )


def changed_urls(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    return {url: pin for url, pin in after.items() if before.get(url) != pin}


def live_test_targets(paths: Iterable[str]) -> list[str]:
    """Keep live behavior checks for changed resource code, not unrelated URLs."""
    changed = set(paths)
    targets = []
    if changed & {
        "src/wgsextract_cli/core/annotation_resources.py",
        "tests/test_remote_resources.py",
    }:
        targets.append("tests/test_remote_resources.py")
    if "tests/test_remote_downloads.py" in changed or any(
        fnmatchcase(path, "src/wgsextract_cli/core/gene_lists.*")
        or fnmatchcase(path, "src/wgsextract_cli/assets/report/gene_lists.*")
        for path in changed
    ):
        targets.append(
            "tests/test_remote_downloads.py::test_built_in_gene_list_downloads_and_matches_its_pins"
        )
    return targets


@cache
def github_assets(owner: str, repo: str, tag: str) -> dict[str, dict[str, Any]]:
    release_id = subprocess.check_output(
        ["gh", "api", f"repos/{owner}/{repo}/releases/tags/{tag}", "--jq", ".id"],
        text=True,
    ).strip()
    pages = json.loads(
        subprocess.check_output(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{owner}/{repo}/releases/{release_id}/assets?per_page=100",
            ],
            text=True,
        )
    )
    return {asset["name"]: asset for page in pages for asset in page}


def _transient(error: OSError) -> bool:
    if isinstance(error, HTTPError):
        return error.code == 429 or 500 <= error.code < 600
    reason = error.reason if isinstance(error, URLError) else error
    return isinstance(
        reason,
        (
            ConnectionRefusedError,
            ConnectionResetError,
            SSLEOFError,
            TimeoutError,
        ),
    )


def _retry_after(error: OSError) -> float:
    if not isinstance(error, HTTPError) or error.headers is None:
        return 0.0
    value = error.headers.get("Retry-After")
    if value is None:
        return 0.0
    value = value.strip()
    if value.isascii() and value.isdecimal():
        return float(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0.0, date.timestamp() - time.time())
    except (ValueError, OverflowError, OSError):
        return 0.0


def _probe_url(url: str) -> int:
    headers = {"Range": "bytes=0-0", "User-Agent": "wgsextract-cli"}
    backoffs = iter((5, 20))
    attempts = 1
    while True:
        try:
            with urlopen(Request(url, headers=headers), timeout=60) as response:
                if not url.startswith("ftp://") and response.status not in (200, 206):
                    raise ValueError(f"HTTP {response.status}")
                try:
                    first_byte = response.read(1)
                except IncompleteRead as exc:
                    raise RemoteDisconnected(
                        "connection closed before response body"
                    ) from exc
                if not first_byte:
                    ranged = getattr(response, "status", None) == 206 and re.fullmatch(
                        r"bytes 0-[0-9]+/(?:[1-9][0-9]*|\*)",
                        response.headers.get("Content-Range", ""),
                    )
                    if getattr(response, "length", 0) or ranged:
                        raise RemoteDisconnected(
                            "connection closed before response body"
                        )
                    raise ValueError("empty response")
        except OSError as exc:
            if isinstance(exc, HTTPError):
                exc.close()
            delay = next(backoffs, None)
            if delay is None or not _transient(exc):
                raise
            retry_after = _retry_after(exc)
            if retry_after > MAX_RETRY_AFTER:
                raise ValueError(
                    f"{exc}; Retry-After exceeds {MAX_RETRY_AFTER}s"
                ) from exc
            time.sleep(max(delay, retry_after))
            attempts += 1
        else:
            return attempts


def check_url(url: str, pin: dict[str, Any]) -> int:
    attempts = _probe_url(url)
    if pin:
        parsed = urlsplit(url)
        parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
        if (
            parsed.netloc != "github.com"
            or len(parts) != 6
            or parts[2:4] != ["releases", "download"]
        ):
            raise ValueError("Integrity metadata requires a GitHub release asset")
        owner, repo, _, _, tag, name = parts
        asset = github_assets(owner, repo, tag).get(name, {})
        if (
            asset.get("digest") != f"sha256:{pin['sha256']}"
            or asset.get("size") != pin["size"]
        ):
            raise ValueError(
                "MIRROR_INTEGRITY: GitHub asset digest/size differs from manifest"
            )
    return attempts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--base", help="PR base SHA; omit for the complete nightly check"
    )
    selection.add_argument(
        "--live-tests-base",
        help="Run live behavior tests affected since the PR base SHA",
    )
    parser.add_argument("--out", type=Path, default=ROOT / "out/remote-urls.json")
    args = parser.parse_args(argv)
    base_sha = args.base or args.live_tests_base
    if base_sha and (
        not all(c in "0123456789abcdef" for c in base_sha) or len(base_sha) != 40
    ):
        parser.error("The PR base must be a full Git commit SHA")
    if args.live_tests_base:
        paths = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", f"{base_sha}...HEAD"],
            cwd=ROOT,
            text=True,
        ).splitlines()
        targets = live_test_targets(paths)
        if not targets:
            print("No changed live-resource behavior tests.")
            return 0
        return subprocess.call(
            [
                sys.executable,
                "-m",
                "pytest",
                "-m",
                "remote",
                "-v",
                *targets,
                "--basetemp",
                str(ROOT / "out/remote-pr"),
            ],
            cwd=ROOT,
        )
    urls = snapshot(ROOT)
    if args.base:
        base = ROOT / "out/remote-base"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(base), args.base], check=True
        )
        try:
            urls = changed_urls(snapshot(base), urls)
        finally:
            subprocess.run(["git", "worktree", "remove", str(base)], check=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(urls, indent=2) + "\n", encoding="utf-8")
    failures = []
    for url, pin in urls.items():
        try:
            attempts = check_url(url, pin)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            from wgsextract_cli.core.resource_mirrors import resources

            entry = resources().get(url)
            rights = (
                f" UPSTREAM_ONLY {entry['id']}: {entry['reason']}"
                if entry and not entry["redistributable"]
                else ""
            )
            failures.append(f"{url}: {exc}{rights}")
            print(f"FAILED {url}: {exc}{rights}", flush=True)
        else:
            retry = f" (after {attempts} attempts)" if attempts > 1 else ""
            print(f"OK {url}{retry}", flush=True)
    print(f"Checked {len(urls)} URLs; {len(failures)} failures.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
