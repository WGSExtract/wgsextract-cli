#!/usr/bin/env python3
"""Probe only changed URLs on PRs; check all sources and mirror pins nightly."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


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


def check_url(url: str, pin: dict[str, Any]) -> None:
    request = Request(
        url, headers={"Range": "bytes=0-0", "User-Agent": "wgsextract-cli"}
    )
    with urlopen(request, timeout=60) as response:
        if not response.read(1):
            raise ValueError("empty response")
        if not url.startswith("ftp://") and response.status not in (200, 206):
            raise ValueError(f"HTTP {response.status}")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", help="PR base SHA; omit for the complete nightly check"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "out/remote-urls.json")
    args = parser.parse_args()
    urls = snapshot(ROOT)
    if args.base:
        if not all(c in "0123456789abcdef" for c in args.base) or len(args.base) != 40:
            parser.error("--base must be a full Git commit SHA")
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
            check_url(url, pin)
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
            print(f"OK {url}", flush=True)
    print(f"Checked {len(urls)} URLs; {len(failures)} failures.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
