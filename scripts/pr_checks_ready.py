#!/usr/bin/env python3
"""Require a passing non-draft area run, not stale draft checks, before landing."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from typing import Any


def checks_ready(
    checks: list[dict[str, Any]],
    *,
    run_id: int | None = None,
    runs: list[dict[str, Any]] | None = None,
) -> bool:
    if runs is not None:
        latest: dict[int, dict[str, Any]] = {}
        known = {int(run["id"]): run for run in runs}
        for metadata in runs:
            workflow = int(metadata["workflow_id"])
            if workflow not in latest or int(metadata["id"]) > int(
                latest[workflow]["id"]
            ):
                latest[workflow] = metadata
        # Do not fall back to an old pass while a replacement has no jobs yet.
        if any(
            run["status"] != "completed"
            or run["conclusion"] not in {"success", "skipped", "neutral"}
            for run in latest.values()
            if run["path"] != ".github/workflows/pr_ci.yml"
        ):
            return False
        current_ids = {int(run["id"]) for run in latest.values()}
        filtered = []
        for check in checks:
            match = re.search(r"/actions/runs/(\d+)(?:/|$)", str(check.get("link", "")))
            if match and check.get("workflow") != "PR area checks":
                check_run = int(match[1])
                if check_run not in known:
                    return False
                if check_run not in current_ids:
                    continue
            filtered.append(check)
        checks = filtered
    if (
        run_id is None
        or not checks
        or any(
            check.get("workflow") != "PR area checks"
            and check.get("bucket") not in {"pass", "skipping"}
            for check in checks
        )
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
        if (
            all(check.get("bucket") in {"pass", "skipping"} for check in run_checks)
            and any(
                check.get("name") == "changes" and check.get("bucket") == "pass"
                for check in run_checks
            )
            and any(
                check.get("name") == "required" and check.get("bucket") == "pass"
                for check in run_checks
            )
            and all(
                check.get("bucket") == "skipping"
                for check in run_checks
                if check.get("name") == "Draft lint and fast tests"
            )
        ):
            return True
    return False


def latest_ready_run(
    runs: list[dict[str, Any]],
    *,
    head_sha: str,
    ready_since: str,
    pr_number: int,
    head_branch: str | None = None,
    head_repository: str | None = None,
) -> int | None:
    titles = {
        f"PR area checks / {action} / ready"
        for action in ("opened", "synchronize", "reopened", "ready_for_review")
    }
    candidates = [
        run
        for run in runs
        # #867: the API reports the run-name ("PR area checks / ACTION / ready")
        # as `name`, so match the workflow by its file.
        if run.get("path") == ".github/workflows/pr_ci.yml"
        and run["event"] == "pull_request"
        and run["head_sha"] == head_sha
        and run["display_title"] in titles
        and datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        >= datetime.fromisoformat(ready_since.replace("Z", "+00:00"))
        and (
            any(pr["number"] == pr_number for pr in run["pull_requests"])
            or (
                not run["pull_requests"]
                and head_branch is not None
                and head_repository is not None
                and run.get("head_branch") == head_branch
                and run.get("head_repository", {}).get("full_name") == head_repository
            )
        )
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda run: int(run["id"]))
    if latest["status"] != "completed" or latest["conclusion"] != "success":
        return None
    return int(latest["id"])


def github_json(*arguments: str) -> Any:
    return json.loads(subprocess.check_output(["gh", *arguments], text=True))


def current_ready_run(
    pr_number: int, repository: str
) -> tuple[int | None, list[dict[str, Any]]]:
    owner, name = repository.split("/")
    query = """
    query($owner:String!, $name:String!, $number:Int!) {
      repository(owner:$owner, name:$name) {
        pullRequest(number:$number) {
          headRefOid headRefName headRepository { nameWithOwner } isDraft createdAt
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
        return None, []
    events = pr["timelineItems"]["nodes"]
    ready_since = events[-1]["createdAt"] if events else pr["createdAt"]
    pages = github_json(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{repository}/actions/runs?head_sha={pr['headRefOid']}&per_page=100",
    )
    runs = [
        run
        for page in pages
        for run in page["workflow_runs"]
        if run["head_sha"] == pr["headRefOid"]
        and run["event"] != "merge_group"
        and (
            run["event"] != "pull_request"
            or any(item["number"] == pr_number for item in run["pull_requests"])
            # GitHub can omit PR associations even for same-repository PR runs.
            or (
                not run["pull_requests"]
                and run.get("head_branch") == pr["headRefName"]
                and run.get("head_repository", {}).get("full_name")
                == pr["headRepository"]["nameWithOwner"]
            )
        )
    ]
    run_id = latest_ready_run(
        runs,
        head_sha=pr["headRefOid"],
        ready_since=ready_since,
        pr_number=pr_number,
        head_branch=pr.get("headRefName"),
        head_repository=(pr.get("headRepository") or {}).get("nameWithOwner"),
    )
    if github_json(*arguments)["data"]["repository"]["pullRequest"] != pr:
        return None, []
    return run_id, runs


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
        run_id, runs = current_ready_run(args.pr, repository)
    except (
        subprocess.CalledProcessError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        print(f"Cannot establish current ready-run validation: {exc}", file=sys.stderr)
        return 1
    if not checks_ready(checks, run_id=run_id, runs=runs):
        print(
            "Checks are incomplete/failing or lack validation for the latest ready run.",
            file=sys.stderr,
        )
        return 1
    print("Non-draft area validation and all reported checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
