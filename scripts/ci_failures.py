#!/usr/bin/env python3
"""Classify CI failures, audit retries, report flakes, and quarantine red main."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from scripts.ci_github import TRUSTED, GitHub
from scripts.ci_quarantine import Quarantine

LEDGER = "<!-- ci-flake-ledger:v1 -->"
TEST_ID = re.compile(r"(?:FAILED|ERROR)\s+([^\s]+\.py::.+?)(?:\s+-\s|$)", re.M)
BAD = {"failure", "cancelled", "timed_out", "startup_failure"}
ROUTED_MAIN = {
    "ubuntu": "ubuntu_ci.yml",
    "macos": "macos_ci.yml",
    "windows": "windows_ci.yml",
    "arm64": "linux_arm64.yml",
    "install": "install_lab.yml",
    "Documentation freshness": "ubuntu_ci.yml",
}


def safe(value: str) -> str:
    return html.escape(value).replace("@", "@\u200b").replace("`", "'")


@dataclass
class Failure:
    job_id: int
    job: str
    step: str
    classification: str
    tests: list[str]
    errors: list[str]
    conclusion: str
    executions: int = 1


def classify(job: dict[str, Any], evidence: str) -> Failure:
    steps = job.get("steps", [])
    failed_steps = [step["name"] for step in steps if step.get("conclusion") in BAD]
    tests = sorted(set(TEST_ID.findall(evidence)))
    kind = "test failure"
    if (
        job.get("conclusion") == "cancelled"
        and not steps
        or re.search(
            r"lost communication with the server|runner has received a shutdown signal",
            evidence,
            re.I,
        )
    ):
        kind = "lost runner"
    elif not tests and re.search(
        r"Upstream package mirror unreachable|Could not resolve host|"
        r"Temporary failure in name resolution|Connection reset by peer|"
        r"HTTP (?:Error )?(?:502|503|504)\b",
        evidence,
        re.I,
    ):
        kind = "mirror/network"
    lines = [
        re.sub(r"^\d{4}-\d\d-\d\dT\S+\s*", "", line).strip()
        for line in evidence.splitlines()
        if re.search(
            r"error|failed|lost communication|shutdown signal|unreachable", line, re.I
        )
    ]
    return Failure(
        int(job["id"]),
        job["name"],
        failed_steps[0] if failed_steps else "(before first step)",
        kind,
        tests,
        [line[:300] for line in lines[:5]],
        str(job["conclusion"]),
    )


class Observer:
    def __init__(self, github: GitHub) -> None:
        self.gh = github
        self._issues: list[dict[str, Any]] | None = None

    @property
    def issues(self) -> list[dict[str, Any]]:
        if self._issues is None:
            self._issues = [
                issue
                for issue in self.gh.pages("issues?state=all&labels=ci-automation")
                if issue["user"]["login"] in TRUSTED and "pull_request" not in issue
            ]
        return self._issues

    def ensure_labels(self) -> None:
        labels = {item["name"] for item in self.gh.pages("labels")}
        for label, color in (("ci-automation", "ededed"), ("ci-red-main", "b60205")):
            if label not in labels:
                self.gh.api("POST", "labels", {"name": label, "color": color})

    def issue(
        self, title: str, marker: str, body: str, *, bug: bool = True
    ) -> dict[str, Any]:
        existing = next(
            (issue for issue in self.issues if marker in (issue.get("body") or "")),
            None,
        )
        content = f"(AI): {marker}\n\n{body}"
        if existing:
            updated = self.gh.api(
                "PATCH",
                f"issues/{existing['number']}",
                {"state": "open", "body": content},
            )
            existing.update(updated)
            return existing
        self.ensure_labels()
        created: dict[str, Any] = self.gh.api(
            "POST",
            "issues",
            {
                "title": title[:240],
                "body": content,
                "labels": ["ci-automation", "bug"] if bug else ["ci-automation"],
            },
        )
        self.issues.append(created)
        # Distinct workflow completions can discover a new flake together.
        # Reconcile by marker, not title/search indexing, keeping the oldest ID.
        matches = [
            item
            for item in self.gh.pages("issues?state=all&labels=ci-automation")
            if item["user"]["login"] in TRUSTED and marker in (item.get("body") or "")
        ]
        canonical = min(matches or [created], key=lambda item: int(item["number"]))
        for duplicate in matches:
            if (
                duplicate["number"] != canonical["number"]
                and duplicate["state"] == "open"
            ):
                self.gh.api(
                    "PATCH", f"issues/{duplicate['number']}", {"state": "closed"}
                )
        self._issues = None
        return canonical

    def comment_once(self, issue: dict[str, Any], marker: str, body: str) -> bool:
        comments = self.gh.pages(f"issues/{issue['number']}/comments")
        if any(
            marker in comment["body"] and comment["user"]["login"] in TRUSTED
            for comment in comments
        ):
            return False
        self.gh.api(
            "POST",
            f"issues/{issue['number']}/comments",
            {"body": f"(AI): {marker}\n\n{body}"},
        )
        return True

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
        runs = self.gh.api(
            "GET", f"actions/workflows/{run['workflow_id']}/runs?{query}&per_page=100"
        )["workflow_runs"]
        return next(
            (
                item
                for item in runs
                if item["head_repository"]["full_name"]
                == (
                    self.gh.repository
                    if branch
                    else run["head_repository"]["full_name"]
                )
            ),
            None,
        )

    def failures(self, run: dict[str, Any], *, history: bool = False) -> list[Failure]:
        latest_jobs: dict[str, dict[str, Any]] = {}
        all_jobs: list[dict[str, Any]] = []
        executions: Counter[str] = Counter()
        # Job-only reruns need not execute unrelated siblings. Retain their last
        # outcomes instead of silently dropping failures from attempt 1.
        for attempt in range(1, run["run_attempt"] + 1):
            jobs = self.gh.pages(
                f"actions/runs/{run['id']}/attempts/{attempt}/jobs", "jobs"
            )
            all_jobs.extend(jobs)
            for job in jobs:
                if job.get("conclusion") == "skipped" and job["name"] in latest_jobs:
                    continue
                latest_jobs[job["name"]] = job
                if job.get("conclusion") != "skipped":
                    executions[job["name"]] += 1
        failures = []
        for job in all_jobs if history else latest_jobs.values():
            if job.get("conclusion") in BAD:
                failure = classify(job, self.gh.evidence(job))
                failure.executions = executions[job["name"]]
                failures.append(failure)
        return failures

    def flaky_marker(self, test: str) -> str:
        return f"<!-- ci-flaky:{hashlib.sha256(test.encode()).hexdigest()} -->"

    def known_flaky(self, test: str) -> bool:
        return any(
            issue["state"] == "open" and self.flaky_marker(test) in issue["body"]
            for issue in self.issues
        )

    def report_recoveries(self, run: dict[str, Any]) -> None:
        if run["run_attempt"] < 2:
            return
        current = self.gh.pages(
            f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs"
        )
        passed = {
            (job["name"], step["name"])
            for job in current
            if job.get("conclusion") == "success"
            for step in job.get("steps", [])
            if step.get("conclusion") == "success"
        }
        # Attempt endpoints bind both observations to the same immutable run SHA.
        previous = dict(run, run_attempt=run["run_attempt"] - 1)
        for failure in self.failures(previous, history=True):
            if (failure.job, failure.step) not in passed:
                continue
            for test in failure.tests:
                issue = self.issue(
                    f"Flaky: {safe(test)}",
                    self.flaky_marker(test),
                    f"`{safe(test)}` failed then passed at SHA `{run['head_sha']}`.\n\n"
                    f"{self.link(run)} — {safe(failure.job)}; "
                    f"passed in attempt {run['run_attempt']}.",
                )
                self.comment_once(
                    issue,
                    f"<!-- ci-recovery:{run['id']}:{run['run_attempt']} -->",
                    f"Failed then passed on the same SHA: {self.link(run)}.",
                )

    def link(self, run: dict[str, Any]) -> str:
        return f"https://github.com/{self.gh.repository}/actions/runs/{run['id']}"

    def red_main(self, run: dict[str, Any], failures: list[Failure]) -> None:
        marker = f"<!-- ci-red-main:{run['workflow_id']} -->"
        tests = sorted(
            {
                test
                for failure in failures
                if failure.classification == "test failure"
                for test in failure.tests
                if not self.known_flaky(test)
            }
        )
        if tests:
            issue = self.issue(
                f"main is red: {run['name']}",
                marker,
                f"{self.link(run)}\n\nCommit `{run['head_sha']}`\n\n"
                + "\n".join(f"- `{safe(test)}`" for test in tests),
            )
            self.comment_once(
                issue,
                f"<!-- ci-red-run:{run['id']}:{run['run_attempt']} -->",
                f"Main is red at `{run['head_sha']}`: {self.link(run)}. Fix before landing PRs.",
            )
        elif run["conclusion"] == "success":
            for issue in self.issues:
                if marker in issue["body"] and issue["state"] == "open":
                    self.gh.api(
                        "PATCH", f"issues/{issue['number']}", {"state": "closed"}
                    )

    def ledger(self) -> dict[str, Any]:
        issue = next((item for item in self.issues if LEDGER in item["body"]), None)
        if issue is None:
            issue = self.issue(
                "CI flake ledger",
                LEDGER,
                "Automatic retry requests and outcomes. Each record includes workflow, job, "
                "step, first errors, classification, SHA and PR. "
                "Use the run links to compare attempts; never erase failed evidence.",
                bug=False,
            )
        pinned = self.gh.request(
            "POST",
            "graphql",
            {
                "query": "query($id:ID!){node(id:$id){... on Issue{isPinned}}}",
                "variables": {"id": issue["node_id"]},
            },
        )["data"]["node"]["isPinned"]
        if pinned:
            return issue
        self.gh.request(
            "POST",
            "graphql",
            {
                "query": "mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{id}}}",
                "variables": {"id": issue["node_id"]},
            },
        )
        return issue

    def rollup(self) -> None:
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        runs = self.gh.pages(
            f"actions/runs?{urlencode({'created': '>=' + since})}", "workflow_runs"
        )
        total = Counter(run["name"] for run in runs)
        retried = Counter(run["name"] for run in runs if run["run_attempt"] > 1)
        ledger = self.ledger()
        self.comment_once(
            ledger,
            f"<!-- ci-week:{now.strftime('%G-%V')} -->",
            f"Seven days since {since}. Run-level retry rate includes manual retries; "
            "individual automatic retry records are below this issue.\n\n"
            "| Workflow | Runs | Retried | Rate |\n|---|---:|---:|---:|\n"
            + "\n".join(
                f"| {safe(name)} | {count} | {retried[name]} | "
                f"{retried[name] / count:.1%} |"
                for name, count in sorted(total.items())
                if name != "Rerun Lost Runners"
            ),
        )

    def main_tests(self, run: dict[str, Any]) -> set[str]:
        main = self.latest(run, "main")
        if not main or main["status"] != "completed" or main["conclusion"] != "failure":
            return set()
        return {
            test
            for failure in self.failures(main)
            if failure.classification == "test failure"
            for test in failure.tests
            if not self.known_flaky(test)
        }

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
            by_workflow: dict[str, set[str]] = {}
            for failure in failures:
                source = str(run["workflow_id"])
                if run["name"] == "PR area checks":
                    source = ROUTED_MAIN.get(failure.job.split(" / ", 1)[0], "")
                if not source or not failure.tests:
                    continue
                if source not in by_workflow:
                    by_workflow[source] = self.main_tests(dict(run, workflow_id=source))
                if (
                    failure.classification == "test failure"
                    and set(failure.tests) & by_workflow[source]
                ):
                    failure.classification = "red main"
        blocked = any(failure.classification == "red main" for failure in failures)
        if blocked:
            print("red main: matching test failures; no retry.")
            self.ensure_labels()
        if blocked or run["conclusion"] == "success":
            Quarantine(self.gh).update(run, active=blocked)
        for failure in failures:
            print(json.dumps(asdict(failure)))
        for failure in failures:
            limit = {"lost runner": 1, "mirror/network": 2, "test failure": 2}.get(
                failure.classification, 0
            )
            if blocked or failure.executions > limit or run["run_attempt"] > 10:
                continue
            if (
                failure.conclusion == "cancelled"
                and failure.classification != "lost runner"
            ):
                continue
            if failure.classification == "test failure" and not rerun_tests:
                continue
            latest = self.latest(run)
            if (
                latest is None
                or latest["id"] != run["id"]
                or latest["run_attempt"] != run["run_attempt"]
                or latest["status"] != "completed"
            ):
                break
            ledger = self.ledger()
            record = {
                "observed_at": run["updated_at"],
                "workflow": run["name"],
                "sha": run["head_sha"],
                "pr": [pr["number"] for pr in run.get("pull_requests", [])],
                "attempt": run["run_attempt"],
                **asdict(failure),
            }
            marker = (
                f"<!-- ci-retry:{run['id']}:{run['run_attempt']}:{failure.job_id} -->"
            )
            if not self.comment_once(
                ledger,
                marker,
                f"Retry requested: {self.link(run)}\n\n<pre>{safe(json.dumps(record, indent=2))}</pre>",
            ):
                continue
            # Audit before mutation: an API error must not produce an invisible retry.
            self.gh.api("POST", f"actions/jobs/{failure.job_id}/rerun")
            self.comment_once(
                ledger, marker + ":accepted", f"Retry accepted: {self.link(run)}."
            )
            # A job rerun starts a new attempt immediately. The next completion
            # handles remaining siblings without racing a running attempt.
            break
        return 2 if blocked else 0


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
            pr = event["pull_request"]
            Quarantine(github).reset(
                pr["number"], pr["head"]["sha"] if pr["state"] == "open" else ""
            )
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
