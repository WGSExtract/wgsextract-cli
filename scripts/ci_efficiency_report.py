"""Measure the last completed UTC week from GitHub Actions and PR metadata."""

from __future__ import annotations

import argparse
import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import urlencode

from scripts.ci_certifications import CONTEXTS, WRITERS
from scripts.ci_github import GitHub

Row = dict[str, Any]
READY = "ReadyForReviewEvent"
DRAFT = "ConvertToDraftEvent"
REMOVED = "RemovedFromMergeQueueEvent"
FORCED = "HeadRefForcePushedEvent"
TIMELINE = """
query($owner:String!, $repo:String!, $number:Int!, $after:String) {
  repository(owner:$owner, name:$repo) { pullRequest(number:$number) {
    timelineItems(first:100, after:$after, itemTypes:[
      READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT,
      REMOVED_FROM_MERGE_QUEUE_EVENT, HEAD_REF_FORCE_PUSHED_EVENT
    ]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        __typename
        ... on ReadyForReviewEvent { createdAt }
        ... on ConvertToDraftEvent { createdAt }
        ... on RemovedFromMergeQueueEvent { createdAt reason }
        ... on HeadRefForcePushedEvent { createdAt afterCommit { oid } }
      }
    }
  }}
}
"""
CHECKS = """
query($ids:[ID!]!) {
  nodes(ids:$ids) { ... on CheckSuite {
    id
    checkRuns(first:100, filterBy:{checkType:ALL}) {
      pageInfo { hasNextPage }
      nodes { name detailsUrl startedAt completedAt conclusion }
    }
  }}
}
"""


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(
        value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else "")
    )


def when(row: Row, key: str) -> datetime | None:
    return stamp(row[key]) if row.get(key) else None


def branch_key(ref: Row) -> tuple[str, str]:
    return ((ref.get("repo") or {}).get("full_name", ""), ref["ref"])


def run_key(run: Row) -> tuple[str, str]:
    return ((run.get("head_repository") or {}).get("full_name", ""), run["head_branch"])


def ready_periods(pr: Row) -> list[tuple[datetime, bool]]:
    changes = sorted(
        (stamp(event["createdAt"]), event["__typename"] == READY)
        for event in pr["timeline"]
        if event["__typename"] in {READY, DRAFT}
    )
    initial_ready = not changes[0][1] if changes else not pr["draft"]
    return [(stamp(pr["created_at"]), initial_ready), *changes]


def run_stage(run: Row, pr: Row | None) -> str:
    event = run["event"]
    if event == "pull_request":
        if pr is None:
            return "PR (readiness unknown)"
        at = stamp(run["created_at"])
        return "Ready PR" if ready_at(ready_periods(pr), at) else "Draft PR"
    if event == "push":
        return "Main push" if run["head_branch"] == "main" else "Other push"
    return {
        "merge_group": "Merge queue",
        "schedule": "Scheduled",
        "release": "Release / manual",
        "workflow_dispatch": "Release / manual",
    }.get(event, "Repository automation")


def ready_at(periods: list[tuple[datetime, bool]], at: datetime) -> bool:
    return next((ready for date, ready in reversed(periods) if date <= at), False)


def push_evidence(pr: Row, runs: list[Row]) -> list[tuple[datetime, str]]:
    # The PR area workflow runs for every head, including skipped draft runs.
    # Other workflows can be path-filtered and can arrive out of order.
    signals = {
        (stamp(run["created_at"]), run["head_sha"])
        for run in runs
        if run["path"].split("@", 1)[0] == ".github/workflows/pr_ci.yml"
    }
    signals.update(
        (stamp(event["createdAt"]), event["afterCommit"]["oid"])
        for event in pr["timeline"]
        if event["__typename"] == FORCED and event.get("afterCommit")
    )
    changes: list[tuple[datetime, str]] = []
    for at, sha in sorted(signals):
        if not changes or changes[-1][1] != sha:
            changes.append((at, sha))
    return changes


def hosted_jobs(run: Row) -> Iterable[tuple[int, datetime, datetime]]:
    # Failed-jobs-only reruns clone unchanged checks with the original clock
    # times. They did not allocate another runner for those jobs.
    seen: set[tuple[str, datetime, datetime]] = set()
    pattern = rf"/actions/runs/{run['id']}/job/\d+$"
    for job in sorted(run["jobs"], key=lambda item: item.get("attempt", 1)):
        if job["conclusion"] == "SKIPPED" or not re.search(
            pattern, job.get("detailsUrl") or ""
        ):
            continue
        start = when(job, "startedAt")
        end = when(job, "completedAt")
        if start is not None and end is not None and end > start:
            key = (job["name"], start, end)
            if key not in seen:
                seen.add(key)
                yield job.get("attempt", 1), start, end


def report(prs: list[Row], runs: list[Row], start: datetime, end: datetime) -> str:
    pr_by_branch: dict[tuple[str, str], list[Row]] = defaultdict(list)
    pr_by_number = {pr["number"]: pr for pr in prs}
    for pr in prs:
        pr_by_branch[branch_key(pr["head"])].append(pr)
    associated: dict[int, Row] = {}
    pr_runs: dict[int, list[Row]] = defaultdict(list)
    for run in runs:
        if run["event"] != "pull_request":
            continue
        at = stamp(run["created_at"])
        candidates = [
            pr_by_number[ref["number"]]
            for ref in run["pull_requests"]
            if ref["number"] in pr_by_number
        ] or pr_by_branch[run_key(run)]
        valid = [
            pr
            for pr in candidates
            if stamp(pr["created_at"]) <= at <= (when(pr, "closed_at") or end)
        ]
        if len(valid) == 1:
            pr = valid[0]
            associated[run["id"]] = pr
            pr_runs[pr["number"]].append(run)

    delays: list[float] = []
    revisions: dict[int, list[tuple[datetime, str]]] = {}
    rework: Counter[str] = Counter()
    for pr in prs:
        number = pr["number"]
        periods = ready_periods(pr)
        first_ready = next((at for at, ready in periods if ready), None)
        merged = when(pr, "merged_at")
        if first_ready is not None and merged is not None and start <= merged < end:
            delays.append((merged - first_ready).total_seconds() / 3600)
        revisions[number] = changes = push_evidence(
            pr, pr_runs[number] + pr.get("prior_runs", [])
        )
        if first_ready is not None:
            rework["pushes"] += sum(
                start <= at < end and first_ready < at for at, _ in changes[1:]
            )
        rework["removals"] += sum(
            event["__typename"] == REMOVED
            and (event["reason"] or "").lower() != "merged"
            and start <= stamp(event["createdAt"]) < end
            for event in pr["timeline"]
        )
        rework["recertifications"] += sum(
            start <= stamp(at) < end for at in pr["recertifications"]
        )

    counts: Counter[str] = Counter()
    seconds: Counter[str] = Counter()
    wasted_seconds: Counter[str] = Counter()
    for run in runs:
        linked_pr = associated.get(run["id"])
        stage = run_stage(run, linked_pr)
        cancelled_run = superseded_run = False
        waste: dict[int, bool] = {}
        for attempt in run.get("attempts", [run]):
            created = stamp(attempt["created_at"])
            completed = stamp(attempt["updated_at"])
            cancelled = attempt["conclusion"] == "cancelled"
            superseded = (
                linked_pr is not None
                and attempt["conclusion"] != "skipped"
                and any(
                    created < at < completed and sha != run["head_sha"]
                    for at, sha in revisions[linked_pr["number"]]
                )
            )
            waste[attempt.get("run_attempt", 1)] = cancelled or superseded
            if start <= created < end:
                cancelled_run |= cancelled
                superseded_run |= superseded
        counts["cancelled"] += cancelled_run
        counts["superseded"] += superseded_run
        counts["wasted"] += cancelled_run or superseded_run
        for attempt_number, job_start, job_end in hosted_jobs(run):
            duration = int((min(job_end, end) - max(job_start, start)).total_seconds())
            if duration > 0:
                seconds[stage] += duration
                if waste[attempt_number]:
                    wasted_seconds[stage] += duration

    latency = "no eligible merges"
    if delays:
        p90 = sorted(delays)[math.ceil(len(delays) * 0.9) - 1]
        latency = f"p50 **{statistics.median(delays):.1f} h**, p90 **{p90:.1f} h**"
    return "\n".join(
        [
            f"(AI): **Weekly CI efficiency: {start:%Y-%m-%d} to {end:%Y-%m-%d} UTC (end exclusive)**",
            "",
            f"- First ready → merged: {latency} (n={len(delays)}).",
            f"- Rework: {rework['pushes']} observed pushes after first ready, "
            f"{rework['recertifications']} re-certified heads, {rework['removals']} queue removals.",
            f"- Wasted runs: {counts['wasted']} distinct "
            f"({counts['cancelled']} cancelled, {counts['superseded']} superseded; may overlap).",
            "",
            "| Stage | Hosted runner-hours | On wasted runs |",
            "| --- | ---: | ---: |",
            *(
                f"| {stage} | {seconds[stage] / 3600:.2f} | {wasted_seconds[stage] / 3600:.2f} |"
                for stage in sorted(seconds)
            ),
            f"| Total | {seconds.total() / 3600:.2f} | {wasted_seconds.total() / 3600:.2f} |",
            "",
            "Merged PRs are sampled by merge date; rework is sampled by event date on PRs into `main`. "
            "Head changes are inferred from PR area runs and force-push events. A re-certification "
            "is the first successful `local-ci/` status on a different head after an earlier head "
            "was certified. Superseded means a newer head appeared before the run ended, not "
            "necessarily that GitHub cancelled it. Wasted hours count time on those runs, "
            "including work before cancellation. Runner-hours use completed job timestamps "
            "from repository workflows (all use hosted runners) and exclude queue waits. "
            "Runs created before the one-day lookback are counted only when found in PR area history.",
        ]
    )


def graphql(api: GitHub, query: str, **variables: Any) -> Row:
    response = api.request("POST", "graphql", {"query": query, "variables": variables})
    if response.get("errors"):
        raise RuntimeError(
            f"GitHub could not read report metadata: {response['errors']}"
        )
    return cast(Row, response["data"])


def timeline(api: GitHub, number: int) -> list[Row]:
    owner, repo = api.repository.split("/")
    cursor = None
    result: list[Row] = []
    while True:
        page = graphql(
            api, TIMELINE, owner=owner, repo=repo, number=number, after=cursor
        )
        items = page["repository"]["pullRequest"]["timelineItems"]
        result.extend(items["nodes"])
        if not items["pageInfo"]["hasNextPage"]:
            return result
        cursor = items["pageInfo"]["endCursor"]


def certifications(
    api: GitHub, heads: set[str], permissions: dict[str, str], end: datetime
) -> list[str]:
    first_success = []
    for sha in sorted(heads):
        statuses = api.pages(f"commits/{sha}/statuses")
        certified = []
        for status in statuses:
            creator = (status.get("creator") or {}).get("login")
            if (
                not creator
                or status["state"] != "success"
                or status["context"] not in CONTEXTS
                or stamp(status["created_at"]) >= end
            ):
                continue
            if creator not in permissions:
                result = api.api("GET", f"collaborators/{creator}/permission")
                permissions[creator] = result["permission"]
            if permissions[creator] in WRITERS:
                certified.append(status["created_at"])
        if certified:
            first_success.append(min(certified))
    return sorted(first_success)[1:]


def prior_area_runs(api: GitHub, pr: Row, lower: datetime) -> list[Row]:
    opened = stamp(pr["created_at"])
    if opened >= lower:
        return []
    key = branch_key(pr["head"])
    filters = urlencode(
        {
            "event": "pull_request",
            "branch": key[1],
            "created": f"{opened:%Y-%m-%dT%H:%M:%SZ}..{lower:%Y-%m-%dT%H:%M:%SZ}",
        }
    )
    return [
        run
        for run in api.pages(
            f"actions/workflows/pr_ci.yml/runs?{filters}", "workflow_runs"
        )
        if run_key(run) == key and stamp(run["created_at"]) < lower
    ]


def rest_jobs(api: GitHub, run: Row) -> list[Row]:
    path = f"actions/runs/{run['id']}"
    return [
        {
            "name": job["name"],
            "detailsUrl": job["html_url"],
            "startedAt": job["started_at"],
            "completedAt": job["completed_at"],
            "conclusion": job["conclusion"].upper() if job["conclusion"] else None,
            "attempt": job["run_attempt"],
        }
        for job in api.pages(f"{path}/jobs?filter=all", "jobs")
        if job["runner_group_name"] == "GitHub Actions"
    ]


def rerun_jobs(api: GitHub, run: Row) -> None:
    path = f"actions/runs/{run['id']}"
    run["attempts"] = [
        api.api("GET", f"{path}/attempts/{attempt}")
        for attempt in range(1, run["run_attempt"])
    ] + [dict(run, created_at=run["run_started_at"] or run["updated_at"])]
    run["jobs"] = rest_jobs(api, run)


def collect(api: GitHub, start: datetime, end: datetime) -> tuple[list[Row], list[Row]]:
    # Workflows starting the previous day may have jobs finishing in the week.
    lower = start - timedelta(days=1)
    days = [lower + timedelta(days=day) for day in range((end - lower).days)]

    def day_runs(day: datetime) -> list[Row]:
        return api.runs_between(day, day + timedelta(days=1))

    with ThreadPoolExecutor(max_workers=4) as pool:
        runs_by_id = {
            run["id"]: run
            for batch in pool.map(day_runs, days)
            for run in batch
            if lower <= stamp(run["created_at"]) < end
            and run["path"].startswith(".github/workflows/")
        }
    heads: dict[tuple[str, str], set[str]] = defaultdict(set)
    for run in runs_by_id.values():
        if run["event"] == "pull_request":
            heads[run_key(run)].add(run["head_sha"])

    prs: list[Row] = []
    permissions: dict[str, str] = {}
    for page in range(1, 101):
        batch = api.api(
            "GET",
            f"pulls?state=all&base=main&sort=updated&direction=desc&per_page=100&page={page}",
        )
        for pr in batch:
            if stamp(pr["updated_at"]) < lower:
                continue
            closed = when(pr, "closed_at")
            if stamp(pr["created_at"]) >= end or (
                closed is not None and closed < lower
            ):
                continue
            shas = heads[branch_key(pr["head"])]
            pr["timeline"] = timeline(api, pr["number"])
            previous = prior_area_runs(api, pr, lower)
            pr["prior_runs"] = previous
            for run in previous:
                if run["run_attempt"] > 1 and stamp(run["updated_at"]) >= start:
                    runs_by_id[run["id"]] = run
            pr["recertifications"] = (
                certifications(
                    api,
                    shas | {run["head_sha"] for run in previous} | {pr["head"]["sha"]},
                    permissions,
                    end,
                )
                if branch_key(pr["head"])[0] == api.repository
                else []
            )
            prs.append(pr)
        if len(batch) < 100 or stamp(batch[-1]["updated_at"]) < lower:
            break
    else:
        raise RuntimeError(
            "PR pagination limit exceeded; refusing an incomplete report"
        )

    runs = list(runs_by_id.values())
    suites: dict[str, list[Row]] = defaultdict(list)
    for run in runs:
        run["jobs"] = []
        if run["run_attempt"] > 1:
            rerun_jobs(api, run)
        elif run["conclusion"] != "skipped":
            suites[run["check_suite_node_id"]].append(run)
    suite_ids = list(suites)
    for offset in range(0, len(suite_ids), 30):
        response = graphql(api, CHECKS, ids=suite_ids[offset : offset + 30])
        for suite_id, suite in zip(
            suite_ids[offset : offset + 30], response["nodes"], strict=True
        ):
            checks = suite["checkRuns"] if suite else None
            for run in suites[suite_id]:
                # Large suites and inaccessible GraphQL nodes use paginated
                # Actions jobs, without truncating the runner-hour totals.
                run["jobs"] = (
                    checks["nodes"]
                    if checks and not checks["pageInfo"]["hasNextPage"]
                    else rest_jobs(api, run)
                )
    return prs, runs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="GitHub owner/repository")
    args = parser.parse_args()
    midnight = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = midnight - timedelta(days=midnight.weekday())
    start = end - timedelta(days=7)
    prs, runs = collect(GitHub(args.repository), start, end)
    print(report(prs, runs, start, end))


if __name__ == "__main__":
    main()
