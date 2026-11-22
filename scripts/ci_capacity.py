#!/usr/bin/env python3
"""Hold `gh pr ready` while hosted runners are needed by the merge queue (#1373).

Read-only: lists this repository's queued and in-progress workflow runs and
their jobs through `gh api`. Exit 0 means a PR may be marked ready; exit 3
means hold. Hold while any merge_group job waits for a runner, or while more
than --max-ready ready-PR area matrices are in flight.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

OK = 0
ERROR = 1
HOLD = 3
AREA_WORKFLOW = ".github/workflows/pr_ci.yml"
ACTIVE_RUN_STATUSES = ("queued", "in_progress")
PAGE = 100
MAX_PAGES = 20

Api = Callable[[str], Any]


def gh_api(path: str) -> Any:
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def pages(api: Api, path: str, key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    separator = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        batch = api(f"{path}{separator}per_page={PAGE}&page={page}")[key]
        items.extend(batch)
        if len(batch) < PAGE:
            return items
    raise RuntimeError(f"More than {MAX_PAGES} pages for {path}; refusing to guess")


@dataclass
class Capacity:
    max_ready: int
    merge_group_queued: int = 0
    merge_group_in_progress: int = 0
    waiting_for_runner: dict[str, int] = field(default_factory=dict)
    ready_matrices: list[dict[str, Any]] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        reasons = []
        if self.merge_group_queued:
            reasons.append(
                f"{self.merge_group_queued} merge_group job(s) waiting for a runner"
            )
        if len(self.ready_matrices) > self.max_ready:
            reasons.append(
                f"{len(self.ready_matrices)} ready-PR matrices in flight"
                f" (max {self.max_ready})"
            )
        return reasons

    @property
    def ok(self) -> bool:
        return not self.reasons

    def report(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": self.reasons, **asdict(self)}

    def summary(self) -> str:
        waiting = sum(self.waiting_for_runner.values())
        state = "OK to mark ready" if self.ok else "HOLD: " + "; ".join(self.reasons)
        return (
            f"{state}. merge_group jobs: {self.merge_group_queued} queued,"
            f" {self.merge_group_in_progress} in progress; jobs waiting for a"
            f" runner: {waiting}; ready matrices in flight:"
            f" {len(self.ready_matrices)}/{self.max_ready}."
        )


def is_ready_matrix(run: dict[str, Any]) -> bool:
    # pr_ci.yml's run-name ends in "/ ready" only for non-draft pull_request events.
    return (
        run.get("event") == "pull_request"
        and str(run.get("path", "")).split("@")[0] == AREA_WORKFLOW
        and str(run.get("display_title", "")).endswith("/ ready")
    )


def measure(repository: str, max_ready: int, api: Api = gh_api) -> Capacity:
    capacity = Capacity(max_ready=max_ready)
    waiting: Counter[str] = Counter()
    runs = {
        run["id"]: run
        for status in ACTIVE_RUN_STATUSES
        for run in pages(
            api, f"repos/{repository}/actions/runs?status={status}", "workflow_runs"
        )
    }
    for run_id, run in sorted(runs.items()):
        jobs = pages(
            api, f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest", "jobs"
        )
        # Jobs are created once their needs resolve, so "queued" means waiting
        # for a runner. Runs with no jobs (stale ghosts) hold no capacity.
        queued = sum(job.get("status") == "queued" for job in jobs)
        running = sum(job.get("status") == "in_progress" for job in jobs)
        if queued:
            waiting[str(run.get("event"))] += queued
        if run.get("event") == "merge_group":
            capacity.merge_group_queued += queued
            capacity.merge_group_in_progress += running
        if is_ready_matrix(run) and (queued or running):
            capacity.ready_matrices.append(
                {
                    "run": run_id,
                    "prs": [pr["number"] for pr in run.get("pull_requests") or []],
                    "url": run.get("html_url"),
                }
            )
    capacity.waiting_for_runner = dict(sorted(waiting.items()))
    return capacity


def wait(
    check: Callable[[], Capacity],
    *,
    timeout: float,
    interval: float,
    max_interval: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = lambda line: print(line, file=sys.stderr),
) -> Capacity:
    """Poll with exponential backoff until capacity frees; poll last at the deadline."""
    deadline = clock() + timeout
    delay = interval
    while True:
        try:
            capacity = check()
        except (subprocess.CalledProcessError, OSError, ValueError, KeyError) as exc:
            pause = min(delay, deadline - clock())
            if pause <= 0:
                raise
            log(f"Capacity check failed, retrying in {pause:.0f}s: {exc}")
        else:
            pause = min(delay, deadline - clock())
            if capacity.ok or pause <= 0:
                return capacity
            log(f"{capacity.summary()} Rechecking in {pause:.0f}s.")
        sleep(pause)
        delay = min(delay * 2, max_interval)


def main(argv: list[str] | None = None, api: Api = gh_api) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="OWNER/REPO; defaults to the current repository")
    parser.add_argument(
        "--max-ready",
        type=int,
        default=4,
        help="hold while more than this many ready-PR matrices are in flight",
    )
    parser.add_argument("--wait", action="store_true", help="poll until OK or timeout")
    parser.add_argument(
        "--timeout", type=float, default=7200, help="--wait limit in seconds"
    )
    parser.add_argument(
        "--interval", type=float, default=60, help="first --wait poll delay in seconds"
    )
    parser.add_argument(
        "--max-interval", type=float, default=600, help="--wait backoff cap in seconds"
    )
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args(argv)
    if args.max_ready < 0 or args.timeout < 0 or args.interval <= 0:
        parser.error("--max-ready/--timeout must be >= 0 and --interval > 0")
    try:
        repository = (
            args.repo
            or json.loads(
                subprocess.check_output(
                    ["gh", "repo", "view", "--json", "nameWithOwner"], text=True
                )
            )["nameWithOwner"]
        )

        def check() -> Capacity:
            return measure(repository, args.max_ready, api)

        capacity = (
            wait(
                check,
                timeout=args.timeout,
                interval=args.interval,
                max_interval=max(args.interval, args.max_interval),
            )
            if args.wait
            else check()
        )
    except (
        subprocess.CalledProcessError,
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
    ) as exc:
        print(f"Cannot read Actions capacity: {exc}", file=sys.stderr)
        return ERROR
    print(json.dumps(capacity.report(), indent=2) if args.json else capacity.summary())
    return OK if capacity.ok else HOLD


if __name__ == "__main__":
    sys.exit(main())
