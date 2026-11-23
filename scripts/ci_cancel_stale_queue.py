#!/usr/bin/env python3
"""Cancel merge-group runs whose queue ref GitHub has deleted (#1373).

When a queued PR is pushed to, dequeued or fails, GitHub deletes its
``gh-readonly-queue`` ref and every later group's ref, then rebuilds them. The
old runs keep holding runners although they can never land. This cancels only
runs of one workflow whose queue branch no longer exists; it never touches its
own run, and any API problem is reported as a warning so the merge group that
runs it is never failed by it.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from scripts.ci_github import GitHub

ACTIVE = ("in_progress", "queued", "waiting", "pending", "requested")
REF_LIMIT = 100


def live_queue_branches(github: GitHub, base: str) -> set[str]:
    prefix = f"heads/gh-readonly-queue/{base}"
    refs = github.api("GET", f"git/matching-refs/{prefix}?per_page={REF_LIMIT}")
    if len(refs) >= REF_LIMIT:
        raise RuntimeError("queue ref list may be truncated")
    return {str(ref["ref"]).removeprefix("refs/heads/") for ref in refs}


def active_runs(github: GitHub, workflow: str) -> list[dict[str, Any]]:
    runs: dict[int, dict[str, Any]] = {}
    for status in ACTIVE:
        path = f"actions/workflows/{workflow}/runs?event=merge_group&status={status}"
        for run in github.pages(path, "workflow_runs"):
            runs[int(run["id"])] = run
    return list(runs.values())


def cancel_stale(
    github: GitHub, *, workflow: str, base: str, run_id: int, own_branch: str
) -> list[int]:
    # Runs first: a run listed before the refs had its ref created earlier, so a
    # live group can never be missing from the later ref snapshot.
    runs = active_runs(github, workflow)
    live = live_queue_branches(github, base)
    if own_branch not in live:
        print(f"::warning::Own queue ref {own_branch} is absent; cancelling nothing.")
        return []
    queue = f"gh-readonly-queue/{base}/"
    cancelled = []
    for run in runs:
        branch = str(run.get("head_branch") or "")
        if int(run["id"]) == run_id or not branch.startswith(queue) or branch in live:
            continue
        try:
            github.api("POST", f"actions/runs/{int(run['id'])}/cancel")
        except Exception as error:  # noqa: BLE001 - never fail the merge group
            print(f"::warning::Could not cancel stale run {run['id']}: {error}")
            continue
        print(f"Cancelled run {run['id']} for deleted queue ref {branch}.")
        cancelled.append(int(run["id"]))
    return cancelled


def main(argv: list[str] | None = None, github: GitHub | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", required=True, help="workflow file name")
    parser.add_argument("--base", required=True, help="queue base branch")
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--ref", required=True, help="this run's queue ref")
    args = parser.parse_args(argv)
    try:
        cancelled = cancel_stale(
            github or GitHub(args.repository),
            workflow=args.workflow,
            base=args.base.removeprefix("refs/heads/"),
            run_id=args.run_id,
            own_branch=args.ref.removeprefix("refs/heads/"),
        )
    except Exception as error:  # noqa: BLE001 - never fail the merge group
        print(f"::warning::Skipped stale merge-group cleanup: {error}")
        return 0
    print(f"Cancelled {len(cancelled)} stale merge-group run(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
