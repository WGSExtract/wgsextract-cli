#!/usr/bin/env python3
"""Hold `gh pr ready` while hosted runners are needed by the merge queue (#1373).

Read-only: inspects this repository's pull requests, workflow runs and jobs
through `gh api`. Exit 0 means a PR may be marked ready; exit 3 means hold.
Pass --pr for a priority PR to bypass the gate (hands-off and land-asap
labels do not change capacity admission). Otherwise hold while a
priority PR has an active area run or is in the merge queue, while any
merge_group job waits for a runner, or while more than --max-ready ready-PR
area matrices are in flight.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

OK = 0
ERROR = 1
HOLD = 3
AREA_WORKFLOW = ".github/workflows/pr_ci.yml"
ACTIVE_RUN_STATUSES = ("queued", "in_progress")
PAGE = 100
MAX_PAGES = 20
# A run without jobs this old is a ghost that never started (seen 11 days stuck).
GHOST_AFTER = timedelta(hours=1)


class Api(Protocol):
    def __call__(self, path: str, /, *args: str) -> Any: ...


def gh_api(path: str, *args: str) -> Any:
    return json.loads(subprocess.check_output(["gh", "api", path, *args], text=True))


def pages(api: Api, path: str, key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    separator = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        batch = api(f"{path}{separator}per_page={PAGE}&page={page}")[key]
        items.extend(batch)
        if len(batch) < PAGE:
            return items
    raise RuntimeError(f"More than {MAX_PAGES} pages for {path}; refusing to guess")


def priority_pulls(repository: str, api: Api) -> Iterator[dict[str, Any]]:
    owner, name = repository.split("/", 1)
    query = """
      query($owner: String!, $name: String!, $cursor: String) {
        repository(owner: $owner, name: $name) {
          pullRequests(first: 100, states: OPEN, labels: ["priority"], after: $cursor) {
            nodes {
              number headRefName headRefOid
              headRepository { nameWithOwner }
              mergeQueueEntry { id }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    """
    fields = ("-f", f"query={query}", "-f", f"owner={owner}", "-f", f"name={name}")
    cursor: tuple[str, ...] = ()
    for _ in range(MAX_PAGES):
        response = api("graphql", *fields, *cursor)
        pulls = response["data"]["repository"]["pullRequests"]
        yield from pulls["nodes"]
        if not pulls["pageInfo"]["hasNextPage"]:
            return
        cursor = ("-f", f"cursor={pulls['pageInfo']['endCursor']}")
    raise RuntimeError(f"Too many pages of priority PRs for {repository}")


@dataclass
class Capacity:
    max_ready: int
    merge_group_queued: int = 0
    merge_group_in_progress: int = 0
    waiting_for_runner: dict[str, int] = field(default_factory=dict)
    ready_matrices: list[dict[str, Any]] = field(default_factory=list)
    priority_bypass: int | None = None
    priority_ci: list[int] = field(default_factory=list)
    priority_queued: list[int] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        reasons = []
        if self.priority_ci:
            numbers = ", ".join(f"#{number}" for number in self.priority_ci)
            reasons.append(f"priority PR(s) {numbers} with active area CI")
        if self.priority_queued:
            numbers = ", ".join(f"#{number}" for number in self.priority_queued)
            reasons.append(f"priority PR(s) {numbers} in the merge queue")
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
        if self.priority_bypass is not None:
            return f"OK to mark ready: priority PR #{self.priority_bypass} skips the capacity gate."
        waiting = sum(self.waiting_for_runner.values())
        state = "OK to mark ready" if self.ok else "HOLD: " + "; ".join(self.reasons)
        return (
            f"{state}. merge_group jobs: {self.merge_group_queued} queued,"
            f" {self.merge_group_in_progress} in progress; jobs waiting for a"
            f" runner: {waiting}; ready matrices in flight:"
            f" {len(self.ready_matrices)}/{self.max_ready}."
        )


def is_pr_area_run(run: dict[str, Any]) -> bool:
    return (
        run.get("event") == "pull_request"
        and str(run.get("path", "")).split("@")[0] == AREA_WORKFLOW
    )


def is_ghost(run: dict[str, Any], jobs: list[dict[str, Any]], now: datetime) -> bool:
    created = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
    return not jobs and now - created > GHOST_AFTER


def area_pr_numbers(
    run: dict[str, Any], priority: dict[int, dict[str, Any]]
) -> set[int]:
    numbers = {pr["number"] for pr in run.get("pull_requests") or []}
    if not numbers:
        # GitHub can omit pull_requests for fork runs; match the exact head.
        run_repo = (run.get("head_repository") or {}).get("full_name", "")
        numbers = {
            number
            for number, pr in priority.items()
            if run_repo
            and run_repo.lower()
            == (pr["headRepository"] or {}).get("nameWithOwner", "").lower()
            and run.get("head_sha") == pr["headRefOid"]
            and run.get("head_branch") == pr["headRefName"]
        }
    return numbers


def measure(
    repository: str,
    max_ready: int,
    api: Api = gh_api,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    *,
    pr: int | None = None,
) -> Capacity:
    capacity = Capacity(max_ready=max_ready)
    priority = {}
    for pull in priority_pulls(repository, api):
        number = pull["number"]
        if number == pr:
            capacity.priority_bypass = pr
            return capacity
        priority[number] = pull
    capacity.priority_queued = sorted(
        number for number, pull in priority.items() if pull["mergeQueueEntry"]
    )
    priority_ci: set[int] = set()
    checked = now()
    waiting: Counter[str] = Counter()
    runs = {
        run["id"]: run
        for status in ACTIVE_RUN_STATUSES
        for run in pages(
            api, f"repos/{repository}/actions/runs?status={status}", "workflow_runs"
        )
    }
    if priority:
        # A replacement PR run can be pending while workflow concurrency
        # cancels its predecessor, before GitHub queues it for a runner.
        for run in pages(
            api,
            f"repos/{repository}/actions/workflows/pr_ci.yml/runs?status=pending",
            "workflow_runs",
        ):
            if is_pr_area_run(run) and not is_ghost(run, [], checked):
                priority_ci.update(area_pr_numbers(run, priority) & priority.keys())
    for run_id, run in sorted(runs.items()):
        jobs = pages(
            api, f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest", "jobs"
        )
        # Jobs are created once their needs resolve, so "queued" means waiting
        # for a runner. A fresh run may not have listed its jobs yet.
        queued = sum(job.get("status") == "queued" for job in jobs)
        running = sum(job.get("status") == "in_progress" for job in jobs)
        if queued:
            waiting[str(run.get("event"))] += queued
        if run.get("event") == "merge_group":
            capacity.merge_group_queued += queued
            capacity.merge_group_in_progress += running
        if is_pr_area_run(run) and not is_ghost(run, jobs, checked):
            numbers = area_pr_numbers(run, priority)
            priority_ci.update(numbers & priority.keys())
            # Only non-draft runs count against the general matrix cap.
            if (queued or running or not jobs) and str(
                run.get("display_title", "")
            ).endswith("/ ready"):
                capacity.ready_matrices.append(
                    {"run": run_id, "prs": sorted(numbers), "url": run.get("html_url")}
                )
    capacity.priority_ci = sorted(priority_ci)
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
    parser.add_argument("--pr", type=int, help="PR number; bypass the gate if priority")
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
    if args.pr is not None and args.pr <= 0:
        parser.error("--pr must be a positive PR number")
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
            return measure(repository, args.max_ready, api, pr=args.pr)

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
