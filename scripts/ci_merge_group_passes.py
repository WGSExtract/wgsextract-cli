"""Fetch the test files a passing merge group already ran at this main SHA (#1373).

A main push lands the exact commit its merge group tested. That group's test
job records the dependency hashes of the test files it executed (not its #915
cache skips) and uploads them as an artifact. Main reads that manifest like
any other passed-hash cache, so it runs only the files the queue skipped as
cached: together, the queue and main run the full suite at the SHA.

Anything uncertain (no matching group, ``required`` not passed, API errors,
a missing or unreadable artifact) writes nothing, and main runs everything.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_test_cache import Manifest  # noqa: E402

WORKFLOW = "pr_ci.yml"
MEMBER = "passed.json"
MAX_PAGES = 10

Api = Callable[[str], bytes]


def gh_api(path: str) -> bytes:
    """The GitHub boundary; gh follows the artifact's signed download redirect."""
    return subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, timeout=120
    ).stdout


def get_json(api: Api, path: str) -> dict[str, Any]:
    data = json.loads(api(path))
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected response from {path}")
    return data


def required_passed(api: Api, repository: str, run_id: int) -> bool:
    """Whether the latest attempt of the run's ``required`` job succeeded."""
    jobs: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        data = get_json(
            api,
            f"repos/{repository}/actions/runs/{run_id}/jobs"
            f"?filter=latest&per_page=100&page={page}",
        )
        jobs += data["jobs"]
        if not data["jobs"] or len(jobs) >= data["total_count"]:
            break
    return any(
        job.get("name") == "required" and job.get("conclusion") == "success"
        for job in jobs
    )


def queue_manifest(
    repository: str, sha: str, artifact: str, *, api: Api = gh_api
) -> tuple[bytes, str] | None:
    """The newest passing merge group's executed-test manifest at ``sha``."""
    query = urlencode({"event": "merge_group", "head_sha": sha, "per_page": 100})
    runs = get_json(
        api, f"repos/{repository}/actions/workflows/{WORKFLOW}/runs?{query}"
    )["workflow_runs"]
    for run in sorted(runs, key=lambda run: int(run["id"]), reverse=True):
        run_id = int(run["id"])
        if not (
            run.get("head_sha") == sha
            and run.get("event") == "merge_group"
            and str(run.get("head_branch", "")).startswith("gh-readonly-queue/main/")
            and (run.get("head_repository") or {}).get("full_name") == repository
            and required_passed(api, repository, run_id)
        ):
            continue
        listed = get_json(
            api,
            f"repos/{repository}/actions/runs/{run_id}/artifacts"
            f"?{urlencode({'name': artifact, 'per_page': 100})}",
        )["artifacts"]
        matches = [
            item
            for item in listed
            if item.get("name") == artifact
            and not item.get("expired")
            and (item.get("workflow_run") or {}).get("head_sha") == sha
        ]
        if not matches:
            continue
        newest = max(matches, key=lambda item: int(item["id"]))
        archive = api(f"repos/{repository}/actions/artifacts/{int(newest['id'])}/zip")
        with zipfile.ZipFile(io.BytesIO(archive)) as files:
            return files.read(MEMBER), str(run.get("html_url") or run_id)
    return None


def fetch(
    repository: str, sha: str, artifact: str, output: Path, *, api: Api = gh_api
) -> str | None:
    """Write a usable manifest to ``output`` and return its run, or write nothing."""
    try:
        found = queue_manifest(repository, sha, artifact, api=api)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
    ) as error:
        print(f"Merge-group lookup failed ({error!r}); running the full suite.")
        return None
    if found is None:
        print("No passing merge group recorded tests at this SHA; running all.")
        return None
    data, run = found
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    if not Manifest.load(output).passed:
        output.unlink()
        print(f"Merge group {run} left no readable manifest; running all.")
        return None
    print(f"Merge group {run} already ran tests at this SHA; main runs the rest.")
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--github-output", type=Path, help="Append run=<id> on a match."
    )
    args = parser.parse_args()
    run = fetch(args.repository, args.sha, args.artifact, args.output)
    if run is not None and args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"run={run.rstrip('/').rsplit('/', 1)[-1]}\n")


if __name__ == "__main__":
    main()
