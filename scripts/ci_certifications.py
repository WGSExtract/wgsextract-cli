"""Read writer attestations, failing closed on every unavailable trust signal."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

PLATFORMS = ("linux-x64", "linux-arm64", "macos-arm64", "macos-x64", "windows-x64")
CONTEXTS = {f"local-ci/{name}" for name in (*PLATFORMS, "generic")}
WRITERS = {"write", "maintain", "admin"}


def gh_api(endpoint: str) -> Any:
    return json.loads(
        subprocess.check_output(["gh", "api", endpoint], text=True, timeout=30)
    )


def certifications(
    repository: str, event: dict[str, Any], api: Callable[[str], Any] = gh_api
) -> set[str]:
    """Only the latest status per context, on a same-repository PR head, counts."""
    pr = event.get("pull_request", {})
    head = pr.get("head", {})
    if (
        not pr
        or (head.get("repo") or {}).get("full_name") != repository
        or not head.get("sha")
    ):
        return set()
    sha = head["sha"]
    valid: set[str] = set()
    try:
        combined = api(f"repos/{repository}/commits/{sha}/status?per_page=100")
        if combined.get("sha") != sha:
            return set()
        seen: set[str] = set()
        permissions: dict[str, str] = {}
        page = 1
        while True:
            statuses = api(
                f"repos/{repository}/commits/{sha}/statuses?per_page=100&page={page}"
            )
            for status in statuses:
                context = status.get("context")
                if context not in CONTEXTS or context in seen:
                    continue
                seen.add(context)
                if status.get("state") != "success":
                    continue
                # Status objects don't have a sha field; their API URL does.
                if not status.get("url", "").endswith(f"/statuses/{sha}"):
                    continue
                creator = (status.get("creator") or {}).get("login")
                if not creator:
                    continue
                if creator not in permissions:
                    permissions[creator] = api(
                        f"repos/{repository}/collaborators/{creator}/permission"
                    ).get("permission", "")
                if permissions[creator] in WRITERS:
                    valid.add(context.removeprefix("local-ci/"))
            if len(statuses) < 100:
                return valid
            page += 1
    except (
        subprocess.SubprocessError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ):
        return set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    valid = certifications(args.repository, json.loads(args.event.read_text()))
    with args.output.open("a", encoding="utf-8") as stream:
        for name in (*PLATFORMS, "generic"):
            stream.write(f"{name.replace('-', '_')}={str(name in valid).lower()}\n")
    print("Writer-certified layers:", ", ".join(sorted(valid)) or "none")


if __name__ == "__main__":
    main()
