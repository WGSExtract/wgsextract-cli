"""Ledger rotation and bounded, non-blocking audit requests."""

from datetime import datetime, timezone

import pytest
from test_ci_failures import ReplayGitHub, ledger_comments, recorded

from scripts.ci_failures import Observer
from scripts.ci_issues import LEDGER


def test_weekly_rotation_archives_an_oversized_iso_year_boundary_ledger() -> None:
    api = ReplayGitHub()
    old = Observer(api).ledger(now=datetime(2027, 1, 3, tzinfo=timezone.utc))
    api.comments[old["number"]] = [
        {
            "id": 10000 + index,
            "body": "Archived audit",
            "user": {"login": "github-actions[bot]"},
        }
        for index in range(10001)
    ]
    Observer(api).rollup(now=datetime(2027, 1, 4, tzinfo=timezone.utc))
    current = next(issue for issue in api.issues if issue["state"] == "open")
    assert "2026-W53" in old["title"]
    assert "2027-W01" in current["title"]
    assert old["node_id"] not in api.pinned
    assert api.issues[0]["state"] == "closed"
    assert current["node_id"] in api.pinned
    assert len(api.comments[old["number"]]) == 10001
    assert api.comment_fetches[old["number"]] == 0


def test_rollup_migrates_the_original_unperiodized_pinned_ledger() -> None:
    api = ReplayGitHub()
    old = api.api(
        "POST",
        "issues",
        {
            "title": "CI flake ledger",
            "body": "<!-- ci-flake-ledger:v1 -->",
            "labels": ["ci-automation"],
        },
    )
    api.pinned.add(old["node_id"])
    Observer(api).rollup(now=datetime(2026, 11, 14, tzinfo=timezone.utc))
    assert api.issues[0]["state"] == "closed"
    assert old["node_id"] not in api.pinned
    current = api.issues[-1]
    assert "2026-W46" in current["body"]
    assert current["node_id"] in api.pinned


def test_multi_job_retry_fetches_comments_once_and_patches_one_audit_record() -> None:
    api = ReplayGitHub()
    first = recorded("test_failure")
    api.jobs[1] = [
        dict(first, id=first["id"] + index, name=f"network job {index}")
        for index in range(10)
    ]
    api.annotation_text = "Upstream package mirror unreachable"
    api.evidence_text = ""
    Observer(api).process(api.run)
    ledger = next(issue for issue in api.issues if LEDGER in issue["body"])
    assert api.comment_fetches[ledger["number"]] == 1
    assert len(api.retried) == 10
    comments = ledger_comments(api)
    assert len(comments) == 1
    assert "Retry accepted:" in comments[0]
    assert "network job 0" in comments[0]
    assert "network job 9" in comments[0]
    Observer(api).process(api.run)
    assert api.comment_fetches[ledger["number"]] == 2
    assert len(api.retried) == 10


def test_oversized_current_ledger_skips_audit_but_still_recovers_lost_runner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = ReplayGitHub()
    ledger = Observer(api).ledger()
    api.comments[ledger["number"]] = [
        {
            "id": 10000 + index,
            "body": "Earlier audit",
            "user": {"login": "github-actions[bot]"},
        }
        for index in range(10001)
    ]
    api.jobs[1] = [recorded("lost_runner")]
    api.run["conclusion"] = "cancelled"
    Observer(api).process(api.run)
    assert api.retried == [api.jobs[1][0]["id"]]
    assert api.comment_fetches[ledger["number"]] == 5
    assert len(api.comments[ledger["number"]]) == 10001
    assert "ci-audit-skipped" in capsys.readouterr().err


def test_unavailable_ledger_does_not_block_recovery_even_when_main_is_red(
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = ReplayGitHub()
    api.jobs[1] = [recorded("lost_runner")]
    api.run["conclusion"] = "cancelled"
    api.main = dict(
        api.run, id=200, head_branch="main", event="push", conclusion="failure"
    )
    api.main_jobs = [recorded("test_failure")]
    api.refuse_issues = True
    Observer(api).process(api.run)
    assert api.retried == [api.jobs[1][0]["id"]]
    assert "ci-audit-skipped" in capsys.readouterr().err


def test_audit_confirmation_failure_does_not_turn_successful_retry_into_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = ReplayGitHub()
    api.jobs[1] = [recorded("lost_runner")]
    api.run["conclusion"] = "cancelled"
    api.refuse_audit_update = True
    assert Observer(api).process(api.run) == 0
    assert api.retried == [api.jobs[1][0]["id"]]
    assert "Retry requested:" in ledger_comments(api)[0]
    assert "ci-audit-skipped (update)" in capsys.readouterr().err


def test_actual_retry_failure_is_not_swallowed_as_an_optional_audit_failure() -> None:
    api = ReplayGitHub()
    api.jobs[1] = [recorded("lost_runner")]
    api.run["conclusion"] = "cancelled"
    api.refuse_retry = True
    with pytest.raises(RuntimeError, match="Actions API"):
        Observer(api).process(api.run)
    assert api.retried == []
    assert "Retry requested:" in ledger_comments(api)[0]
    api.refuse_retry = False
    Observer(api).process(api.run)
    assert api.retried == [api.jobs[1][0]["id"]]
    assert len(ledger_comments(api)) == 1
    assert "Retry accepted:" in ledger_comments(api)[0]
