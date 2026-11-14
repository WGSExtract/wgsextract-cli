"""Replay the lost Tauri producer and missing-artifact consumer from main."""

from __future__ import annotations

import copy
from typing import Any

from scripts.ci_failures import Observer
from scripts.ci_github import Evidence
from tests.test_ci_failures import ReplayGitHub, ledger_comments, recorded


class InstallLabReplay(ReplayGitHub):
    def __init__(self) -> None:
        super().__init__()
        self.run = recorded("lost_upstream_run")
        self.jobs = {1: recorded("lost_upstream_jobs")["jobs"]}
        self.evidence_by_job = {
            131964780747: Evidence(
                failures="\n".join(
                    item["message"]
                    for item in recorded("lost_upstream_annotations")
                    if item["annotation_level"] == "failure"
                ),
                log=recorded("lost_upstream_errors")["log"],
            ),
            132035104536: Evidence(
                failures="\n".join(
                    item["message"]
                    for item in recorded("lost_upstream_annotations_attempt_2")
                    if item["annotation_level"] == "failure"
                ),
            ),
        }

    def evidence(self, job: dict[str, Any]) -> Evidence:
        if int(job["id"]) in self.evidence_by_job:
            return self.evidence_by_job[int(job["id"])]
        return super().evidence(job)


def test_770_missing_artifact_from_lost_upstream_reruns_the_whole_run_once() -> None:
    api = InstallLabReplay()
    assert Observer(api).process(api.run) == 0
    assert api.rerun_scopes == ["rerun"]
    assert {131947396559, 131964780747} <= set(api.retried)
    audit = ledger_comments(api)[0]
    assert audit.count("lost runner") == 2
    assert "tauri-macos-arm64" in audit
    assert not any(issue["title"].startswith("main is red:") for issue in api.issues)

    api.run["run_attempt"] = 2
    api.jobs[2] = recorded("lost_upstream_jobs_attempt_2")["jobs"]
    Observer(api).process(api.run)
    assert api.rerun_scopes == ["rerun"]


def test_unrelated_test_failure_is_not_hidden_by_a_lost_artifact_producer() -> None:
    api = InstallLabReplay()
    api.jobs[1].append(recorded("test_failure"))
    Observer(api).process(api.run)
    assert api.retried == []
    assert any(issue["title"].startswith("main is red:") for issue in api.issues)


def test_missing_different_artifact_is_not_blindly_treated_as_runner_loss() -> None:
    api = InstallLabReplay()
    evidence = api.evidence_by_job[131964780747]
    api.evidence_by_job[131964780747] = Evidence(
        failures=evidence.failures.replace("tauri-macos-arm64", "different-artifact"),
        log=evidence.log,
    )
    Observer(api).process(api.run)
    assert api.retried == []


def test_artifact_error_in_a_non_download_step_is_not_promoted() -> None:
    api = InstallLabReplay()
    consumer = next(job for job in api.jobs[1] if job["id"] == 131964780747)
    next(step for step in consumer["steps"] if step["conclusion"] == "failure")[
        "name"
    ] = "Run native tests"
    Observer(api).process(api.run)
    assert api.retried == []


def test_repeated_consumer_failures_do_not_spend_the_unretried_root_budget() -> None:
    api = InstallLabReplay()
    api.run["run_attempt"] = 2
    api.jobs[2] = [
        job
        for job in recorded("lost_upstream_jobs_attempt_2")["jobs"]
        if job["name"] != "app-build / Tauri (tauri-macos-arm64)"
    ]
    Observer(api).process(api.run)
    assert api.rerun_scopes == ["rerun"]
    assert 131947396559 in api.retried
    assert 132035104536 in api.retried


def test_a_successful_producer_is_not_an_infrastructure_excuse() -> None:
    api = InstallLabReplay()
    root = next(job for job in api.jobs[1] if job["id"] == 131947396559)
    root.update(
        conclusion="success", steps=[{"name": "Build", "conclusion": "success"}]
    )
    Observer(api).process(api.run)
    assert api.retried == []


def test_main_lost_dependency_does_not_quarantine_an_unrelated_pr_failure() -> None:
    api = InstallLabReplay()
    api.main = copy.deepcopy(api.run)
    api.main_jobs = copy.deepcopy(api.jobs[1])
    api.run.update(
        id=100,
        head_branch="feature",
        event="pull_request",
        pull_requests=[{"number": 123}],
    )
    api.jobs[1] = [job for job in api.jobs[1] if job["id"] == 131964780747]
    assert Observer(api).process(api.run) == 0
    assert "ci-red-main" not in api.labels
