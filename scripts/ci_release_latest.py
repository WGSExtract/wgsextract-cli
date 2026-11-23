"""Decide whether a CLI release tag may become the repository's latest release.

Prints ``true`` or ``false`` for ``gh release create --latest``. The tag
becomes the latest unless a newer vX.Y.Z release is already published. A
failed lookup is never read as "no newer release" (#1229): the lookup is
retried within a bound and otherwise exits non-zero without a decision.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

VERSION_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
ATTEMPTS = 4
TIMEOUT_SECONDS = 60


class LookupFailed(RuntimeError):
    pass


def version(tag: str) -> tuple[int, ...] | None:
    match = VERSION_TAG.fullmatch(tag)
    return tuple(map(int, match.groups())) if match else None


def stable_tags(pages: object) -> list[str]:
    """Published, non-prerelease tags; any unexpected shape is a failed lookup."""
    if not isinstance(pages, list) or not pages:
        raise ValueError("expected a list of release pages")
    tags = []
    for page in pages:
        if not isinstance(page, list):
            raise ValueError(f"expected a page of releases, got {page!r:.200}")
        for release in page:
            if not (
                isinstance(release, dict)
                and isinstance(release.get("tag_name"), str)
                and isinstance(release.get("draft"), bool)
                and isinstance(release.get("prerelease"), bool)
            ):
                raise ValueError(f"unexpected release {release!r:.200}")
            if not release["draft"] and not release["prerelease"]:
                tags.append(release["tag_name"])
    return tags


def published_tags(repository: str) -> list[str]:
    """Tags of every published, non-prerelease release, from all pages."""
    command = [
        "gh",
        "api",
        "--paginate",
        "--slurp",
        f"repos/{repository}/releases?per_page=100",
    ]
    error = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
            )
            if result.returncode == 0:
                return stable_tags(json.loads(result.stdout))
            error = result.stderr.strip() or f"gh exited {result.returncode}"
        except subprocess.TimeoutExpired:
            error = f"timed out after {TIMEOUT_SECONDS}s"
        except ValueError as exc:
            error = f"unexpected response: {exc!r}"
        if attempt < ATTEMPTS:
            delay = 5 * 2**attempt
            print(
                f"Listing releases failed (attempt {attempt}/{ATTEMPTS}): {error}; "
                f"retrying in {delay}s.",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise LookupFailed(f"Couldn't list releases after {ATTEMPTS} attempts: {error}")


def becomes_latest(tag: str, published: list[str]) -> bool:
    own = version(tag)
    if own is None:
        raise ValueError(f"{tag} isn't a vX.Y.Z release tag")
    newer = {v: other for other in published if (v := version(other)) and v > own}
    if newer:
        print(
            f"{tag} won't be the latest: {newer[max(newer)]} is newer.", file=sys.stderr
        )
    return not newer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/repository")
    parser.add_argument("tag", help="the vX.Y.Z tag being released")
    args = parser.parse_args(argv)
    try:
        latest = becomes_latest(args.tag, published_tags(args.repo))
    except (LookupFailed, ValueError) as exc:
        print(
            f"::error::{exc}. Not publishing {args.tag}; rerun the release.",
            file=sys.stderr,
        )
        return 1
    print("true" if latest else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
