#!/usr/bin/env python3
"""Classify CI failures, audit retries, report flakes, and quarantine red main."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from scripts.ci_github import GitHub
from scripts.ci_issues import IssueReports
from scripts.ci_policy import (
    BAD,
    ROUTED_MAIN,
    SUMMARY_JOBS,
    Failure,
    classify,
    classify_lost_dependencies,
    matches,
    retryable,
    safe,
)
from scripts.ci_quarantine import Quarantine


class Observer(IssueReports):
    def __init__(self, github: GitHub) -> None:
        super().__init__(github)
        self._jobs: dict[tuple[int, int], list[dict[str, Any]]] = {}

    def jobs(self, run: dict[str, Any], attempt: int) -> list[dict[str, Any]]:
        key = int(run["id"]), attempt
        if key not in self._jobs:
            self._jobs[key] = self.gh.pages(
                f"actions/runs/{run['id']}/attempts/{attempt}/jobs", "jobs"
            )
        return self._jobs[key]

    def scope(self, run: dict[str, Any]) -> str:
        jobs = self.jobs(run, run["run_attempt"])
        if not any("(label event)" in job["name"] for job in jobs):
            return "code"
        return (
            "install-label"
            if any(job["name"].startswith("install / ") for job in jobs)
            else "ignored-label"
        )

    def latest(
        self, run: dict[str, Any], branch: str | None = None
    ) -> dict[str, Any] | None:
        query = urlencode(
            {
                "branch": branch or run["head_branch"],
                "event": "push" if branch else run["event"],
                **({"status": "completed"} if branch else {}),
            }
        )
        runs: list[dict[str, Any]] = self.gh.api(
            "GET", f"actions/workflows/{run['workflow_id']}/runs?{query}&per_page=100"
        )["workflow_runs"]
        for item in runs:
            repository = (
                self.gh.repository if branch else run["head_repository"]["full_name"]
            )
            if item["head_repository"]["full_name"] != repository:
                continue
            if branch and item["conclusion"] not in {"failure", "success"}:
                continue
            if (
                not branch
                and run["name"] == "PR area checks"
                and item["id"] != run["id"]
                and item["head_sha"] == run["head_sha"]
                and self.scope(item) != self.scope(run)
            ):
                continue
            return item
        return None

    def failures(self, run: dict[str, Any], *, history: bool = False) -> list[Failure]:
        latest_jobs: dict[str, tuple[dict[str, Any], int]] = {}
        all_jobs: list[tuple[dict[str, Any], int]] = []
        executions: Counter[str] = Counter()
        seen: dict[int, int] = {}
        counted: set[int] = set()
        for attempt in range(1, run["run_attempt"] + 1):
            for job in self.jobs(run, attempt):
                job_id = int(job["id"])
                actual_attempt = seen.setdefault(
                    job_id, int(job.get("run_attempt", attempt))
                )
                all_jobs.append((job, actual_attempt))
                if job.get("conclusion") == "skipped" and job["name"] in latest_jobs:
                    continue
                previous = latest_jobs.get(job["name"])
                if previous is not None and actual_attempt < previous[1]:
                    continue
                latest_jobs[job["name"]] = job, actual_attempt
                if job.get("conclusion") != "skipped" and job_id not in counted:
                    counted.add(job_id)
                    executions[job["name"]] += 1
        failures = []
        for job, attempt in all_jobs if history else latest_jobs.values():
            if job.get("conclusion") in BAD:
                failure = classify(job, self.gh.evidence(job))
                failure.executions = executions[job["name"]]
                failure.source_attempt = attempt
                failures.append(failure)
        # A summary gate fails with its dependencies and reruns with them, so
        # it must not turn a retryable runner loss into a mixed failure.
        if any(failure.job not in SUMMARY_JOBS for failure in failures):
            failures = [f for f in failures if f.job not in SUMMARY_JOBS]
        if not history:
            classify_lost_dependencies(failures)
        return failures

    def flaky_marker(self, test: str) -> str:
        return f"<!-- ci-flaky:{hashlib.sha256(test.encode()).hexdigest()} -->"

    def known_flaky(self, test: str) -> bool:
        issue = self.find(self.flaky_marker(test))
        return issue is not None and issue["state"] == "open"

    def report_recoveries(self, run: dict[str, Any]) -> None:
        if (
            run["run_attempt"] < 2
            or run["head_repository"]["full_name"] != self.gh.repository
        ):
            return
        passed = {
            (job["name"], step["name"])
            for job in self.jobs(run, run["run_attempt"])
            if job.get("conclusion") == "success"
            and int(job.get("run_attempt", run["run_attempt"])) == run["run_attempt"]
            for step in job.get("steps", [])
            if step.get("conclusion") == "success"
        }
        previous = dict(run, run_attempt=run["run_attempt"] - 1)
        recovery = f"<!-- ci-recovery:{run['id']}:{run['run_attempt']} -->"
        for failure in self.failures(previous, history=True):
            if (failure.job, failure.step) not in passed:
                continue
            for test in failure.tests:
                marker = self.flaky_marker(test)
                existing = self.find(marker)
                if existing and self.has_comment(existing, recovery):
                    continue
                issue = self.issue(
                    f"Flaky: {safe(test)}",
                    marker,
                    f"`{safe(test)}` failed then passed at SHA `{run['head_sha']}`.\n\n"
                    f"{self.link(run)} — {safe(failure.job)}; "
                    f"passed in attempt {run['run_attempt']}.",
                )
                self.comment_once(
                    issue,
                    recovery,
                    f"Failed then passed on the same SHA: {self.link(run)}.",
                )

    def link(self, run: dict[str, Any]) -> str:
        return f"https://github.com/{self.gh.repository}/actions/runs/{run['id']}"

    def non_flaky(self, failures: list[Failure]) -> list[Failure]:
        result = []
        for failure in failures:
            if failure.classification != "test failure":
                continue
            if failure.tests:
                tests = [test for test in failure.tests if not self.known_flaky(test)]
                if not tests:
                    continue
                failure.tests = tests
            result.append(failure)
        return result

    def red_main(self, run: dict[str, Any], failures: list[Failure]) -> None:
        marker = f"<!-- ci-red-main:{run['workflow_id']} -->"
        current = self.non_flaky(failures)
        if current:
            details: list[str] = []
            for failure in current:
                details.extend(f"- `{safe(test)}`" for test in failure.tests)
                if not failure.tests:
                    details.append(
                        f"- {safe(failure.job)} / {safe(failure.step)}: "
                        + safe(
                            "; ".join(failure.errors) or "No detailed error available"
                        )
                    )
            issue = self.issue(
                f"main is red: {run['name']}",
                marker,
                f"{self.link(run)}\n\nCommit `{run['head_sha']}`\n\n"
                + "\n".join(details),
            )
            self.comment_once(
                issue,
                f"<!-- ci-red-run:{run['id']}:{run['run_attempt']} -->",
                f"Main is red at `{run['head_sha']}`: {self.link(run)}. Fix before landing PRs.",
            )
        elif run["conclusion"] == "success":
            existing = self.find(marker)
            if existing and existing["state"] == "open":
                self.gh.api(
                    "PATCH", f"issues/{existing['number']}", {"state": "closed"}
                )

    def main_failures(self, run: dict[str, Any]) -> list[Failure]:
        main = self.latest(run, "main")
        if not main or main["conclusion"] != "failure":
            return []
        return self.non_flaky(self.failures(main))

    def audit_retry(self, run: dict[str, Any], failures: list[Failure]) -> None:
        if len(failures) == 1 and failures[0].source_attempt == run["run_attempt"]:
            endpoint = f"actions/jobs/{failures[0].job_id}/rerun"
            scope = "job"
        else:
            scope = (
                "rerun"
                if any(f.conclusion == "cancelled" for f in failures)
                else "rerun-failed-jobs"
            )
            endpoint = f"actions/runs/{run['id']}/{scope}"
        marker = f"<!-- ci-retry:{run['id']}:{run['run_attempt']} -->"
        records = [
            {
                "observed_at": run["updated_at"],
                "workflow": run["name"],
                "sha": run["head_sha"],
                "pr": [pr["number"] for pr in run.get("pull_requests", [])],
                "attempt": run["run_attempt"],
                "scope": scope,
                **asdict(failure),
            }
            for failure in failures
        ]
        details = f"\n\n<pre>{safe(json.dumps(records, indent=2))}</pre>"
        body = f"(AI): {marker}\n\nRetry requested: {self.link(run)}{details}"
        receipt = self.begin_retry_audit(marker, body)
        if receipt.duplicate:
            return
        latest = self.latest(run)
        if (
            not latest
            or latest["id"] != run["id"]
            or latest["status"] != "completed"
            or latest["run_attempt"] != run["run_attempt"]
            or (
                run.get("pull_requests")
                and not any(
                    Quarantine(self.gh).head(int(pr["number"])) == run["head_sha"]
                    for pr in run["pull_requests"]
                )
            )
        ):
            self.finish_retry_audit(
                receipt,
                f"(AI): {marker}\n\nRetry not dispatched: the source run or PR "
                f"changed while its audit was recorded.{details}",
            )
            return
        self.gh.api("POST", endpoint)
        self.finish_retry_audit(
            receipt, f"(AI): {marker}\n\nRetry accepted: {self.link(run)}{details}"
        )

    def process(self, run: dict[str, Any], *, rerun_tests: bool = False) -> int:
        self.report_recoveries(run)
        latest = self.latest(run)
        if latest is None or latest["id"] != run["id"]:
            print("Superseded run; not rerunning or changing main status.")
            return 0
        if (
            latest["run_attempt"] != run["run_attempt"]
            or latest["status"] != "completed"
        ):
            print("Attempt is no longer current.")
            return 0
        failures = self.failures(run) if run["conclusion"] != "success" else []
        is_main = (
            run["head_branch"] == "main"
            and run["event"] == "push"
            and run["head_repository"]["full_name"] == self.gh.repository
        )
        if is_main:
            self.red_main(run, failures)
        elif failures:
            baselines: dict[str, list[Failure]] = {}
            for failure in failures:
                if failure.classification != "test failure":
                    continue
                source = str(run["workflow_id"])
                if run["name"] == "PR area checks":
                    source = ROUTED_MAIN.get(failure.job.split(" / ", 1)[0], "")
                if not source:
                    continue
                if source not in baselines:
                    baselines[source] = self.main_failures(
                        dict(run, workflow_id=source)
                    )
                if matches(failure, baselines[source]):
                    failure.classification = "red main"
        blocked = any(failure.classification == "red main" for failure in failures)
        if blocked:
            print("red main: matching failures; no retry.")
            self.ensure_labels()
        if blocked or run["conclusion"] == "success":
            Quarantine(self.gh).update(run, active=blocked)
        for failure in failures:
            print(json.dumps(asdict(failure)))
        if blocked:
            return 2
        if not failures or run["run_attempt"] >= 10:
            return 0
        # A mixed run needs a code fix, not an infrastructure retry that will
        # strand old job IDs or restart deliberately cancelled queued siblings.
        if not all(retryable(failure, rerun_tests=rerun_tests) for failure in failures):
            return 0
        self.audit_retry(run, failures)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--event", type=Path)
    source.add_argument("--run-id", type=int)
    source.add_argument("--rollup", action="store_true")
    parser.add_argument(
        "--rerun-tests", action="store_true", help="Audited landing-script retries"
    )
    args = parser.parse_args()
    github = GitHub(args.repository)
    if args.event:
        event = json.loads(args.event.read_text())
        if "pull_request" in event:
            Quarantine(github).reset(event["pull_request"]["number"])
            return 0
    observer = Observer(github)
    if args.rollup:
        observer.rollup()
        return 0
    run = (
        json.loads(args.event.read_text())["workflow_run"]
        if args.event
        else github.api("GET", f"actions/runs/{args.run_id}")
    )
    result = observer.process(run, rerun_tests=args.rerun_tests)
    return 0 if args.event else result


if __name__ == "__main__":
    sys.exit(main())
