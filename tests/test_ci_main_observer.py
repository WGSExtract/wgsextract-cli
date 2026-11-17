"""Main status must survive retries and refresh after path-filtered pushes."""

from __future__ import annotations

import pytest
from test_ci_failures import ReplayGitHub, passed_job, recorded

from scripts.ci_failures import Observer


@pytest.mark.parametrize("attempt", [2, 3])
def test_903_cancelled_retry_keeps_pr_quarantined_while_successor_is_queued(
    attempt: int,
) -> None:
    api = ReplayGitHub()
    newer, cancelled = recorded("superseded_main_runs")
    api.main = dict(cancelled, run_attempt=attempt)
    api.other_runs = [newer]
    api.last_completed_main = dict(
        cancelled, id=90, conclusion="success", run_attempt=1
    )
    api.main_jobs = [recorded("test_failure")]
    api.attempt_runs[cancelled["id"], 1] = dict(
        cancelled, conclusion="failure", run_attempt=1
    )
    if attempt == 3:
        api.attempt_runs[cancelled["id"], 2] = dict(
            cancelled, conclusion="cancelled", run_attempt=2
        )

    assert Observer(api).process(api.run, rerun_tests=True) == 2
    assert "ci-red-main" in api.labels
    assert api.retried == []


def test_903_green_nightly_closes_red_main_and_refreshes_pr_baseline() -> None:
    api = ReplayGitHub()
    api.run = recorded("main_run")
    observer = Observer(api)
    observer.process(api.run)
    bug = api.issues[0]
    failed = dict(api.run)
    api.run = dict(failed, id=failed["id"] + 1, event="schedule", conclusion="success")
    api.last_completed_main = failed
    observer.process(api.run)
    assert bug["state"] == "closed"

    api.main = dict(api.run)
    api.main_jobs = [recorded("test_failure")]
    api.run = dict(failed, id=100, head_branch="feature", event="pull_request")
    api.run["pull_requests"] = [{"number": 123}]
    assert observer.process(api.run) == 0
    assert "ci-red-main" not in api.labels
    assert api.retried == []


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_903_independent_cancelled_retry_still_reports_confirmed_flake(
    event: str,
) -> None:
    api = ReplayGitHub()
    api.run.update(event=event, run_attempt=2, conclusion="cancelled")
    api.jobs[2] = [
        passed_job(),
        dict(recorded("lost_runner"), name="other job"),
    ]
    api.other_runs = [dict(api.run, id=999, run_attempt=1)]
    Observer(api).process(api.run)
    assert any(issue["title"].startswith("Flaky:") for issue in api.issues)
    assert api.retried == []


def test_1038_green_nightly_reports_recovery_while_successor_is_queued() -> None:
    api = ReplayGitHub()
    api.run.update(head_branch="main", event="push", pull_requests=[])
    observer = Observer(api)
    observer.process(api.run)
    bug = api.issues[0]
    api.run.update(event="schedule", conclusion="success")
    api.other_runs = [
        dict(api.run, id=999, event="push", status="queued", conclusion=None)
    ]
    observer.process(api.run)
    assert bug["state"] == "closed"


@pytest.mark.parametrize("conclusion", ["success", "failure"])
@pytest.mark.parametrize("successor_status", ["queued", "in_progress"])
def test_1038_conclusive_main_verdict_survives_a_continuous_push_backlog(
    conclusion: str, successor_status: str
) -> None:
    api = ReplayGitHub()
    api.run = recorded("main_run")
    observer = Observer(api)
    observer.process(api.run)
    bug = api.issues[0]
    api.run = dict(api.run, id=api.run["id"] + 1, conclusion=conclusion)
    api.other_runs = [
        dict(
            api.run,
            id=api.run["id"] + 1,
            status=successor_status,
            conclusion=None,
        )
    ]

    observer.process(api.run, rerun_tests=True)

    assert bug["state"] == ("closed" if conclusion == "success" else "open")
    if conclusion == "failure":
        marker = f"<!-- ci-red-run:{api.run['id']}:{api.run['run_attempt']} -->"
        assert any(marker in comment["body"] for comment in api.comments[bug["number"]])
    assert api.retried == []


@pytest.mark.parametrize("conclusion", ["success", "failure"])
def test_1038_delayed_main_verdict_cannot_overwrite_a_newer_completed_run(
    conclusion: str,
) -> None:
    api = ReplayGitHub()
    api.run = recorded("main_run")
    observer = Observer(api)
    observer.process(api.run)
    bug = api.issues[0]
    older = dict(api.run, conclusion=conclusion)
    api.run = dict(
        api.run,
        id=api.run["id"] + 1,
        conclusion="failure" if conclusion == "success" else "success",
    )
    observer.process(api.run)
    expected = bug["state"]
    api.other_runs = [dict(api.run)]
    api.run = older

    observer.process(api.run)

    assert bug["state"] == expected
    assert api.retried == []
