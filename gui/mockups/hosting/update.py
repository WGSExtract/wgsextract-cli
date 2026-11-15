#!/usr/bin/env python3
"""Fast-forward the dedicated app checkout, switch to main after merge, restart."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def restart(domain: str) -> None:
    subprocess.run(
        ["/bin/launchctl", "kickstart", "-k", domain + "/org.wgsextract.mockups"],
        check=True,
    )


def update(root: Path, domain: str) -> None:
    app = root / "app"

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(app), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout.strip()

    if git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("app has local edits; refusing automatic update")
    branch = git("branch", "--show-current")
    git("fetch", "--quiet", "origin", "+refs/heads/main:refs/remotes/origin/main")
    merged = (
        subprocess.run(
            [
                "git",
                "-C",
                str(app),
                "merge-base",
                "--is-ancestor",
                "HEAD",
                "refs/remotes/origin/main",
            ],
            check=False,
        ).returncode
        == 0
    )
    if branch != "main" and not merged:
        query = urlencode({"head": "WGSExtract:" + branch, "state": "closed"})
        try:
            with urlopen(
                "https://api.github.com/repos/WGSExtract/wgsextract-cli/pulls?" + query,
                timeout=20,
            ) as response:
                merged = any(pr.get("merged_at") for pr in json.load(response))
        except OSError as err:
            print("merge-status check unavailable:", err, flush=True)
    if branch != "main" and merged:
        exists = (
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(app),
                    "show-ref",
                    "--verify",
                    "--quiet",
                    "refs/heads/main",
                ],
                check=False,
            ).returncode
            == 0
        )
        if exists:
            git("checkout", "main")
            git("merge", "--ff-only", "refs/remotes/origin/main")
        else:
            git("checkout", "-b", "main", "refs/remotes/origin/main")
    elif branch == "main":
        git("merge", "--ff-only", "refs/remotes/origin/main")
    else:
        git("fetch", "--quiet", "origin", branch)
        git("merge", "--ff-only", "FETCH_HEAD")
    revision = git("rev-parse", "HEAD")
    marker = root / "app-restarted.sha"
    restarted = marker.read_text("utf-8").strip() if marker.exists() else ""
    if restarted != revision:
        restart(domain)
        pending = marker.with_suffix(".partial")
        pending.write_text(revision + "\n", encoding="utf-8")
        pending.replace(marker)
    print(
        "app",
        git("rev-parse", "--short", "HEAD"),
        git("branch", "--show-current"),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/Users/wgse/mockup-server"))
    parser.add_argument("--domain", default="user/502")
    args = parser.parse_args()
    update(args.root, args.domain)
