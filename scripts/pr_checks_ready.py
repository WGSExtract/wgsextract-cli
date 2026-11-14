#!/usr/bin/env python3
"""Require a passing non-draft area run, not stale draft checks, before landing."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from typing import Any


def checks_ready(checks: list[dict[str, Any]], *, run_id: int | None = None) -> bool:
    if (
        run_id is None
        or not checks
        or any(check.get("bucket") not in {"pass", "skipping"} for check in checks)
    ):
        return False
    for gate in checks:
        if (
            gate.get("name") != "Ready PR area checks"
            or gate.get("workflow") != "PR area checks"
            or gate.get("bucket") != "pass"
        ):
            continue
        run, separator, _job = str(gate.get("link", "")).rpartition("/job/")
        if not separator or not run.endswith(f"/actions/runs/{run_id}"):
            continue
        run_checks = [
            check
            for check in checks
            if check.get("workflow") == "PR area checks"
            and str(check.get("link", "")).startswith(f"{run}/job/")
        ]
        if any(
            check.get("name") == "changes" and check.get("bucket") == "pass"
            for check in run_checks
        ) and all(
            check.get("bucket") == "skipping"
            for check in run_checks
            if check.get("name") == "Draft lint and fast tests"
        ):
            return True
    return False


def latest_ready_run(
    runs: list[dict[str, Any]],
    *,
    head_sha: str,
    ready_since: str,
    pr_number: int,
) -> int | None:
    titles = {
        f"PR area checks / {action} / ready"
        for action in ("opened", "synchronize", "reopened", "ready_for_review")
    }
    candidates = [
        run
        for run in runs
        if run["name"] == "PR area checks"
        and run["event"] == "pull_request"
        and run["head_sha"] == head_sha
        and run["display_title"] in titles
        and datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        >= datetime.fromisoformat(ready_since.replace("Z", "+00:00"))
        and any(pr["number"] == pr_number for pr in run["pull_requests"])
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda run: int(run["id"]))
    if latest["status"] != "completed" or latest["conclusion"] != "success":
        return None
    return int(latest["id"])


def github_json(*arguments: str) -> Any:
    return json.loads(subprocess.check_output(["gh", *arguments], text=True))


def current_ready_run(pr_number: int, repository: str) -> int | None:
    owner, name = repository.split("/")
    query = """
    query($owner:String!, $name:String!, $number:Int!) {
      repository(owner:$owner, name:$name) {
        pullRequest(number:$number) {
          headRefOid isDraft createdAt
          timelineItems(last:1, itemTypes:[READY_FOR_REVIEW_EVENT]) {
            nodes { ... on ReadyForReviewEvent { id createdAt } }
          }
        }
      }
    }
    """
    arguments = (
        "api",
        "graphql",
        "-f",
        f"query={query}",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
        "-F",
        f"number={pr_number}",
    )
    pr = github_json(*arguments)["data"]["repository"]["pullRequest"]
    if not pr or pr["isDraft"]:
        return None
    events = pr["timelineItems"]["nodes"]
    ready_since = events[-1]["createdAt"] if events else pr["createdAt"]
    pages = github_json(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{repository}/actions/workflows/pr_ci.yml/runs"
        f"?event=pull_request&head_sha={pr['headRefOid']}&per_page=100",
    )
    run_id = latest_ready_run(
        [run for page in pages for run in page["workflow_runs"]],
        head_sha=pr["headRefOid"],
        ready_since=ready_since,
        pr_number=pr_number,
    )
    if github_json(*arguments)["data"]["repository"]["pullRequest"] != pr:
        return None
    return run_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--repo", help="OWNER/REPO; defaults to the current repository")
    args = parser.parse_args()
    try:
        checks = json.load(sys.stdin)
        if not isinstance(checks, list) or not checks:
            raise ValueError("Check report is empty or invalid.")
        repository = (
            args.repo
            or github_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]
        )
        run_id = current_ready_run(args.pr, repository)
    except (
        subprocess.CalledProcessError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        print(f"Cannot establish current ready-run validation: {exc}", file=sys.stderr)
        return 1
    if not checks_ready(checks, run_id=run_id):
        print(
            "Checks are incomplete/failing or lack validation for the latest ready run.",
            file=sys.stderr,
        )
        return 1
    print("Non-draft area validation and all reported checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
