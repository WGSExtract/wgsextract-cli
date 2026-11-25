"""Fixture-backed regressions for the weekly CI report (#1554)."""

from datetime import datetime

from scripts.ci_efficiency_report import (
    Row,
    certifications,
    collect,
    report,
    rerun_jobs,
    stamp,
)
from scripts.ci_github import GitHub


def pr(
    number: int,
    *,
    opened: str,
    merged: str | None,
    timeline: list[Row] | None = None,
) -> Row:
    return {
        "number": number,
        "head": {"ref": f"branch-{number}", "repo": {"full_name": "owner/repo"}},
        "draft": False,
        "created_at": opened,
        "merged_at": merged,
        "closed_at": merged,
        "timeline": timeline or [],
        "recertifications": [],
    }


def run(
    run_id: int,
    created: str,
    stopped: str,
    *,
    sha: str = "first",
    event: str = "pull_request",
    conclusion: str = "success",
    jobs: list[Row] | None = None,
) -> Row:
    return {
        "id": run_id,
        "head_repository": {"full_name": "owner/repo"},
        "head_branch": "branch-1",
        "head_sha": sha,
        "path": ".github/workflows/pr_ci.yml",
        "created_at": created,
        "updated_at": stopped,
        "pull_requests": [],
        "event": event,
        "conclusion": conclusion,
        "jobs": jobs or [],
    }


def job(run_id: int, start: str, end: str, job_id: int = 1) -> Row:
    return {
        "name": "Linux tests",
        "detailsUrl": f"https://github.com/owner/repo/actions/runs/{run_id}/job/{job_id}",
        "startedAt": start,
        "completedAt": end,
        "conclusion": "SUCCESS",
    }


def test_1554_reports_first_ready_and_rework_without_counting_same_head_reruns() -> (
    None
):
    opened, ready, redraft, reopened, push, merged = (
        "2026-11-16T00:00:00Z",
        "2026-11-17T00:00:00Z",
        "2026-11-17T12:00:00Z",
        "2026-11-18T00:00:00Z",
        "2026-11-18T06:00:00Z",
        "2026-11-19T00:00:00Z",
    )
    first = pr(
        1,
        opened=opened,
        merged=merged,
        timeline=[
            {"__typename": "ReadyForReviewEvent", "createdAt": ready},
            {"__typename": "ConvertToDraftEvent", "createdAt": redraft},
            {"__typename": "ReadyForReviewEvent", "createdAt": reopened},
            {
                "__typename": "RemovedFromMergeQueueEvent",
                "createdAt": push,
                "reason": "failed_checks",
            },
            {
                "__typename": "RemovedFromMergeQueueEvent",
                "createdAt": merged,
                "reason": "merged",
            },
        ],
    )
    first["recertifications"] = ["2026-11-18T08:00:00Z"]
    second = pr(2, opened=opened, merged="2026-11-17T00:00:00Z")
    runs = [
        run(1, opened, ready, conclusion="skipped"),
        run(2, ready, redraft),
        run(3, reopened, push),
        run(4, push, merged, sha="second"),
    ]
    body = report([first, second], runs, stamp(opened), stamp("2026-11-23T00:00:00Z"))
    assert "p50 **36.0 h**, p90 **48.0 h** (n=2)" in body
    assert (
        "1 observed pushes after first ready, 1 re-certified heads, 1 queue removals"
        in body
    )


def test_1554_wasted_runs_count_once_and_reused_jobs_do_not_add_runner_hours() -> None:
    start = "2026-11-16T00:00:00Z"
    prep = "2026-11-15T23:00:00Z"
    during = "2026-11-16T01:00:00Z"
    new_head = "2026-11-16T02:00:00Z"
    stop = "2026-11-16T03:00:00Z"
    end = "2026-11-23T00:00:00Z"
    cancelled = run(
        1,
        start,
        stop,
        conclusion="cancelled",
        jobs=[
            job(1, during, stop),
            job(1, during, stop, job_id=2),
            dict(job(1, during, stop, job_id=3), attempt=2),
            dict(job(1, during, stop, job_id=4), attempt=2),
        ],
    )
    cancelled["attempts"] = [
        {
            "created_at": start,
            "updated_at": stop,
            "conclusion": "cancelled",
            "run_attempt": 1,
        },
        {
            "created_at": "2026-11-16T04:00:00Z",
            "updated_at": "2026-11-16T05:00:00Z",
            "conclusion": "success",
            "run_attempt": 2,
        },
    ]
    runs = [
        cancelled,
        run(2, new_head, stop, sha="second", jobs=[job(2, new_head, stop)]),
        run(3, prep, during, event="merge_group", jobs=[job(3, prep, during)]),
        run(4, end, end, event="schedule"),
    ]
    body = report([pr(1, opened=prep, merged=None)], runs, stamp(start), stamp(end))
    assert "1 distinct (1 cancelled, 1 superseded" in body
    assert "| Ready PR | 5.00 | 4.00 |" in body
    assert "| Merge queue | 1.00 | 0.00 |" in body
    assert "| Total | 6.00 | 4.00 |" in body
    assert "no eligible merges (n=0)" in body


def test_1554_reports_prior_head_and_does_not_charge_old_attempt_for_late_rerun() -> (
    None
):
    opened = "2026-11-08T00:00:00Z"
    previous = "2026-11-12T00:00:00Z"
    start = "2026-11-16T00:00:00Z"
    pushed = "2026-11-16T04:00:00Z"
    reran = "2026-11-16T06:00:00Z"
    stopped = "2026-11-16T07:00:00Z"
    end = "2026-11-23T00:00:00Z"
    long_lived = pr(1, opened=opened, merged=None)
    long_lived["prior_runs"] = [run(1, opened, previous)]
    earlier = run(
        2,
        start,
        stopped,
        conclusion="cancelled",
        jobs=[
            job(2, start, "2026-11-16T01:00:00Z"),
            dict(job(2, reran, stopped, job_id=2), attempt=2),
        ],
    )
    earlier["run_attempt"] = 2
    earlier["attempts"] = [
        {
            "created_at": start,
            "updated_at": "2026-11-16T02:00:00Z",
            "conclusion": "success",
            "run_attempt": 1,
        },
        {
            "created_at": reran,
            "updated_at": stopped,
            "conclusion": "cancelled",
            "run_attempt": 2,
        },
    ]
    body = report(
        [long_lived],
        [earlier, run(3, pushed, stopped, sha="next")],
        stamp(start),
        stamp(end),
    )
    assert "1 observed pushes after first ready" in body
    assert "1 distinct (1 cancelled, 0 superseded" in body
    assert "| Ready PR | 2.00 | 1.00 |" in body


def test_1554_intermediate_retry_uses_its_own_start_for_weekly_cancellation() -> None:
    opened = "2026-11-14T00:00:00Z"
    start = "2026-11-16T00:00:00Z"
    stopped = "2026-11-16T01:00:00Z"
    final = "2026-11-16T02:00:00Z"
    end = "2026-11-23T00:00:00Z"
    original = dict(
        run(1, opened, final, event="schedule"), run_attempt=3, run_started_at=final
    )

    class FixtureGitHub(GitHub):
        def request(self, method: str, path: str, data: Row | None = None) -> Row:
            if "/jobs?" in path:
                return {"jobs": []}
            number = int(path.rsplit("/", 1)[1])
            return {
                "run_attempt": number,
                "created_at": opened,
                "run_started_at": opened if number == 1 else start,
                "updated_at": opened if number == 1 else stopped,
                "conclusion": "success" if number == 1 else "cancelled",
            }

    rerun_jobs(FixtureGitHub("owner/repo"), original)
    body = report([], [original], stamp(start), stamp(end))
    assert "1 distinct (1 cancelled, 0 superseded" in body


def test_1554_recertification_requires_a_writer_and_known_context() -> None:
    def status(when: str, author: str, context: str = "local-ci/generic") -> Row:
        return {
            "created_at": f"2026-11-{when}T02:00:00Z",
            "creator": {"login": author},
            "context": context,
            "state": "success",
        }

    responses = {
        "commits/old/statuses": [status("10", "writer")],
        "commits/new/statuses": [status("17", "writer"), status("18", "writer")],
        "commits/fake/statuses": [
            status("19", "writer", "local-ci/not-a-platform"),
            status("20", "reader"),
        ],
    }

    class FixtureGitHub(GitHub):
        def pages(
            self, path: str, key: str | None = None, *, max_pages: int = 100
        ) -> list[Row]:
            return responses[path]

        def api(self, method: str, path: str, data: Row | None = None) -> Row:
            return {
                "permission": {"writer": "write", "reader": "read"}[path.split("/")[1]]
            }

    assert certifications(
        FixtureGitHub("owner/repo"),
        {"fake", "new", "old"},
        {},
        stamp("2026-11-23T00:00:00Z"),
    ) == ["2026-11-17T02:00:00Z"]


def test_1554_large_check_suite_uses_paginated_runner_jobs() -> None:
    start = stamp("2026-11-16T00:00:00Z")
    stop = "2026-11-16T01:00:00Z"
    end = stamp("2026-11-23T00:00:00Z")
    fixture_run = dict(
        run(1, start.isoformat(), stop, event="schedule"),
        run_attempt=1,
        check_suite_node_id="suite",
    )

    class FixtureGitHub(GitHub):
        def runs_between(self, lower: datetime, upper: datetime) -> list[Row]:
            return [fixture_run] if lower == start else []

        def request(
            self, method: str, path: str, data: Row | None = None
        ) -> Row | list[Row]:
            if path == "graphql":
                return {
                    "data": {
                        "nodes": [
                            {
                                "checkRuns": {
                                    "pageInfo": {"hasNextPage": True},
                                    "nodes": [],
                                }
                            }
                        ]
                    }
                }
            if "/pulls?" in path:
                return []
            hosted = {
                "name": "Linux tests",
                "html_url": job(1, start.isoformat(), stop)["detailsUrl"],
                "started_at": start.isoformat(),
                "completed_at": stop,
                "conclusion": "success",
                "runner_group_name": "GitHub Actions",
                "run_attempt": 1,
            }
            return {
                "jobs": [
                    hosted,
                    dict(hosted, name="Local", runner_group_name="Self-hosted"),
                ]
            }

    prs, runs = collect(FixtureGitHub("owner/repo"), start, end)
    assert "| Scheduled | 1.00 | 0.00 |" in report(prs, runs, start, end)
