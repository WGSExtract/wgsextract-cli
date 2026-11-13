"""Observed API edge cases from the independent and Copilot reviews."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from test_ci_failures import ReplayGitHub, ledger_comments, passed_job, recorded

from scripts.ci_failures import Observer
from scripts.ci_quarantine import Quarantine


@pytest.mark.parametrize(
    "quoted",
    [
        "The hosted runner lost communication with the server",
        "Connection reset by peer",
    ],
)
def test_test_tracebacks_cannot_turn_test_failures_into_infrastructure_retries(
    quoted: str,
) -> None:
    api = ReplayGitHub()
    api.evidence_text += "\nAssertionError: expected " + quoted
    Observer(api).process(api.run)
    assert api.retried == []


def test_successful_mirror_fallback_warning_cannot_hide_actual_test_failure_log() -> (
    None
):
    api = ReplayGitHub()
    api.use_transport = True
    api.annotations.append(
        {
            "annotation_level": "warning",
            "message": "Upstream package mirror unreachable; successfully using cached packages",
        }
    )
    Observer(api).process(api.run)
    assert api.retried == []
    Observer(api).process(api.run, rerun_tests=True)
    assert "test_a_resumed_chunked_realign" in ledger_comments(api)[0]
    assert "test failure" in ledger_comments(api)[0]


def test_cancelled_main_run_does_not_replace_last_conclusive_failure() -> None:
    api = ReplayGitHub()
    api.main = dict(
        api.run, id=201, head_branch="main", event="push", conclusion="cancelled"
    )
    api.last_completed_main = dict(api.main, id=200, conclusion="failure")
    api.main_jobs = [recorded("test_failure")]
    assert Observer(api).process(api.run, rerun_tests=True) == 2
    assert api.retried == []


def test_fork_recovery_cannot_create_global_flake_exemptions() -> None:
    api = ReplayGitHub()
    api.run.update(run_attempt=2, conclusion="success")
    api.run["head_repository"] = {"full_name": "untrusted/fork"}
    api.jobs[2] = [passed_job()]
    Observer(api).process(api.run)
    assert api.issues == []


def lint_failure(api: ReplayGitHub) -> None:
    api.evidence_text = "error: invalid import in scripts/tool.py:42"
    api.jobs[1][0]["name"] = "lint"
    for step in api.jobs[1][0]["steps"]:
        if step["conclusion"] == "failure":
            step["name"] = "Ruff check"


def test_non_pytest_main_failure_names_job_step_error_and_commit_in_bug() -> None:
    api = ReplayGitHub()
    lint_failure(api)
    api.run.update(head_branch="main", event="push", pull_requests=[])
    Observer(api).process(api.run)
    bug = api.issues[0]
    assert bug["title"].startswith("main is red:")
    assert "lint / Ruff check" in bug["body"]
    assert "invalid import" in bug["body"]
    assert api.run["head_sha"] in bug["body"]


def test_matching_non_pytest_pr_failure_is_not_retried_as_a_flake() -> None:
    api = ReplayGitHub()
    lint_failure(api)
    api.main = dict(api.run, id=200, head_branch="main", event="push")
    api.main_jobs = copy.deepcopy(api.jobs[1])
    assert Observer(api).process(api.run, rerun_tests=True) == 2
    assert api.retried == []


@pytest.mark.parametrize("infrastructure", [True, False])
def test_same_head_label_only_router_run_does_not_supersede_real_validation(
    infrastructure: bool,
) -> None:
    api = ReplayGitHub()
    api.run["name"] = "PR area checks"
    api.jobs[1][0]["name"] = "macos / test-macos"
    api.other_runs = [dict(api.run, id=999, conclusion="success")]
    api.run_jobs[999] = [
        {
            "id": 9999,
            "name": "Documentation freshness (label event)",
            "conclusion": "skipped",
            "steps": [],
        }
    ]
    if infrastructure:
        api.annotation_text = "The hosted runner lost communication with the server"
        api.evidence_text = ""
        Observer(api).process(api.run)
        assert len(api.retried) == 1
    else:
        api.main = dict(
            api.run, id=200, name="CI macOS", head_branch="main", event="push"
        )
        api.main_by_workflow["macos_ci.yml"] = api.main
        api.main_jobs = [recorded("test_failure")]
        assert Observer(api).process(api.run, rerun_tests=True) == 2
        assert "ci-red-main" in api.labels


def test_label_run_for_a_new_head_still_supersedes_old_validation() -> None:
    api = ReplayGitHub()
    api.run["name"] = "PR area checks"
    api.other_runs = [dict(api.run, id=999, head_sha="b" * 40)]
    api.annotation_text = "The hosted runner lost communication with the server"
    Observer(api).process(api.run)
    assert api.retried == []


def test_old_cancelled_sibling_is_retried_by_run_not_rejected_old_job_id() -> None:
    api = ReplayGitHub()
    first = recorded("lost_runner")
    second = dict(first, id=first["id"] + 1, name="other cancelled job")
    api.jobs[1] = [first, second]
    api.jobs[2] = [dict(first, conclusion="success")]
    api.run.update(run_attempt=2, conclusion="cancelled")
    Observer(api).process(api.run)
    assert api.rerun_scopes == ["rerun"]
    assert second["id"] in api.retried
    assert second["name"] in ledger_comments(api)[0]


def test_failed_infrastructure_siblings_are_retried_together_and_each_audited() -> None:
    api = ReplayGitHub()
    first = api.jobs[1][0]
    second = dict(first, id=first["id"] + 1, name="other network job")
    api.jobs[1].append(second)
    api.annotation_text = "Upstream package mirror unreachable"
    api.evidence_text = ""
    Observer(api).process(api.run)
    assert api.rerun_scopes == ["rerun-failed-jobs"]
    assert api.retried == [first["id"], second["id"]]
    assert len(ledger_comments(api)) == 1
    assert "Retry accepted:" in ledger_comments(api)[0]
    assert second["name"] in ledger_comments(api)[0]


def test_concurrent_committed_quarantine_is_reconciled_after_stale_label_delete() -> (
    None
):
    api = ReplayGitHub()
    api.run["conclusion"] = "success"
    api.labels.add("ci-red-main")

    def competing_failure() -> None:
        state = {"workflow": 987, "sha": api.run["head_sha"], "active": True}
        api.api(
            "POST",
            "issues/123/comments",
            {"body": f"(AI): <!-- ci-quarantine {json.dumps(state)} -->"},
        )

    api.on_label_delete = competing_failure
    Observer(api).process(api.run)
    assert "ci-red-main" in api.labels


@pytest.mark.parametrize("closed", [True, False])
def test_late_old_head_observer_cannot_restore_label_after_cleanup(
    closed: bool,
) -> None:
    api = ReplayGitHub()
    api.main = dict(api.run, id=200, head_branch="main", event="push")
    api.main_jobs = [recorded("test_failure")]
    api.labels.add("ci-red-main")
    if closed:
        api.pr_state = "closed"
    else:
        api.pr_sha = "b" * 40
    Quarantine(api).reset(123)
    assert "ci-red-main" not in api.labels
    Observer(api).process(api.run)
    assert "ci-red-main" not in api.labels
    assert api.comments.get(123, []) == []


def test_deliberately_cancelled_started_job_prevents_restarting_queued_sibling() -> (
    None
):
    api = ReplayGitHub()
    api.run["conclusion"] = "cancelled"
    api.jobs[1][0]["conclusion"] = "cancelled"
    api.jobs[1].append(dict(recorded("lost_runner"), name="queued sibling"))
    Observer(api).process(api.run, rerun_tests=True)
    assert api.retried == []
    assert api.issues == []


def test_closed_newer_duplicate_is_not_reopened_instead_of_canonical_bug() -> None:
    api = ReplayGitHub()
    marker = "<!-- ci-flaky:collision -->"
    original = api.api(
        "POST",
        "issues",
        {"title": "Flaky: same test", "body": marker, "labels": ["ci-automation"]},
    )
    duplicate = api.api(
        "POST",
        "issues",
        {"title": "Flaky: same test", "body": marker, "labels": ["ci-automation"]},
    )
    api.api("PATCH", f"issues/{duplicate['number']}", {"state": "closed"})
    issue = Observer(api).issue("Flaky: same test", marker, "new evidence")
    assert issue["number"] == original["number"]
    assert [item["number"] for item in api.issues if item["state"] == "open"] == [
        original["number"]
    ]


def test_replayed_recovery_does_not_reopen_resolved_bug_but_new_evidence_does() -> None:
    api = ReplayGitHub()
    api.run.update(run_attempt=2, conclusion="success")
    api.jobs[2] = [passed_job()]
    Observer(api).process(api.run)
    bug = api.issues[0]
    api.api("PATCH", f"issues/{bug['number']}", {"state": "closed"})
    Observer(api).process(api.run)
    assert bug["state"] == "closed"
    api.run["id"] = 101
    Observer(api).process(api.run)
    assert bug["state"] == "open"
    assert len(api.issues) == 1


def test_busy_week_is_partitioned_past_api_cap_and_boundary_runs_are_deduplicated() -> (
    None
):
    api = ReplayGitHub()
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    start = now - timedelta(days=1)
    api.runs = [
        dict(
            api.run,
            id=1000 + index,
            run_attempt=2 if index % 2 else 1,
            created_at=(start + timedelta(minutes=index)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        )
        for index in range(1001)
    ]
    Observer(api).rollup(now=now)
    assert "| 1001 | 500 |" in ledger_comments(api)[0]


def test_attempt_ten_does_not_dispatch_an_eleventh_attempt() -> None:
    api = ReplayGitHub()
    api.run.update(run_attempt=10, conclusion="cancelled")
    api.jobs[1] = [recorded("lost_runner")]
    Observer(api).process(api.run)
    assert api.retried == []
    assert api.issues == []


def test_push_during_audit_cannot_restart_a_superseded_head() -> None:
    api = ReplayGitHub()
    api.annotation_text = "The hosted runner lost communication with the server"

    def newer_push() -> None:
        api.other_runs = [dict(api.run, id=999, head_sha="b" * 40, status="queued")]

    api.on_audit = newer_push
    Observer(api).process(api.run)
    assert api.retried == []
    assert any("not dispatched" in comment for comment in ledger_comments(api))


@pytest.mark.parametrize("closed", [True, False])
def test_pr_change_during_audit_blocks_retry_before_new_run_exists(
    closed: bool,
) -> None:
    api = ReplayGitHub()
    api.annotation_text = "The hosted runner lost communication with the server"

    def changed_pr() -> None:
        if closed:
            api.pr_state = "closed"
        else:
            api.pr_sha = "b" * 40

    api.on_audit = changed_pr
    Observer(api).process(api.run)
    assert api.retried == []
    assert any("not dispatched" in comment for comment in ledger_comments(api))
