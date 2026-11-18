"""Resolve immutable, trusted app sources before Install Lab executes a build."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Any
from urllib.parse import quote

REPOSITORIES = {"WGSExtract/wgsextract-cli", "WGSExtract/gui-for-cli"}


def github_json(endpoint: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        subprocess.check_output(["gh", "api", endpoint], text=True)
    )
    return payload


def resolve_revision(
    repository: str, run_id: str, release: str, expected_sha: str
) -> str:
    """Only this workflow's commit, or a published release tag, may execute."""
    if repository not in REPOSITORIES:
        raise ValueError("App repository is not trusted")
    if repository == "WGSExtract/gui-for-cli":
        raise ValueError(
            "Legacy app releases from WGSExtract/gui-for-cli are unsupported: "
            "they lack a version-bound Install Lab contract. Choose a published "
            "WGSExtract/wgsextract-cli release containing scripts/installlab."
        )
    if run_id:
        if not re.fullmatch(r"[0-9]+", run_id) or release:
            raise ValueError("Choose a numeric app run or a release, not both")
        run = github_json(f"repos/{repository}/actions/runs/{run_id}")
        if (
            repository != "WGSExtract/wgsextract-cli"
            or run.get("head_repository", {}).get("full_name") != repository
            or run.get("path") != ".github/workflows/gui-distribution.yml"
            or run.get("event") not in {"push", "schedule", "workflow_dispatch"}
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("head_sha") != expected_sha
        ):
            raise ValueError(
                "App run must be a successful first-party GUI distribution "
                "of this workflow's exact commit; use --ref BRANCH for an "
                "unmerged build"
            )
        revision = expected_sha
    else:
        if not release:
            raise ValueError("An app run or published release is required")
        tag = quote(release, safe="")
        published = github_json(f"repos/{repository}/releases/tags/{tag}")
        if published.get("draft") or published.get("tag_name") != release:
            raise ValueError("App release must be published")
        obj = github_json(f"repos/{repository}/git/ref/tags/{tag}")["object"]
        for _ in range(8):
            if obj["type"] != "tag":
                break
            obj = github_json(f"repos/{repository}/git/tags/{obj['sha']}")["object"]
        if obj["type"] != "commit":
            raise ValueError("App release tag must resolve to a commit")
        revision = obj["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("App source must resolve to an immutable commit")
    return revision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run", default="")
    parser.add_argument("--release", default="")
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    try:
        print(
            resolve_revision(args.repository, args.run, args.release, args.expected_sha)
        )
    except (ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"App source rejected: {error}\n")


if __name__ == "__main__":
    main()
