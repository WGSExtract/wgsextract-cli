"""Replay trimmed GitHub responses; only the GitHub API boundary is replaced."""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.ci_failures import Observer
from scripts.ci_github import Evidence, GitHub
from scripts.ci_issues import LEDGER
from scripts.ci_quarantine import Quarantine

FIXTURES = Path(__file__).parent / "fixtures" / "ci_failures"


def recorded(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class ReplayGitHub(GitHub):
    """Stateful GitHub boundary: issues, labels and retries are observable results."""

    def __init__(self) -> None:
        super().__init__("WGSExtract/wgsextract-cli")
        self.run: dict[str, Any] = recorded("main_run")
        self.run.update(
            id=100, head_branch="feature", event="pull_request", run_attempt=1
        )
        self.run["pull_requests"] = [{"number": 123}]
        self.main: dict[str, Any] | None = None
        self.last_completed_main: dict[str, Any] | None = None
        self.main_by_workflow: dict[str, dict[str, Any]] = {}
        self.other_runs: list[dict[str, Any]] = []
        self.run_jobs: dict[int, list[dict[str, Any]]] = {}
        self.jobs: dict[int, list[dict[str, Any]]] = {1: [recorded("test_failure")]}
        self.main_jobs: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.labels: set[str] = set()
        self.repository_labels: set[str] = set()
        self.retried: list[int] = []
        self.rerun_scopes: list[str] = []
        self.pinned: set[str] = set()
        self.evidence_text = str(recorded("errors")["log"])
        self.annotations: list[dict[str, Any]] = recorded("errors")["annotations"]
        self.annotation_text = "\n".join(
            item["message"]
            for item in self.annotations
            if item["annotation_level"] == "failure"
        )
        self.use_transport = False
        self.pr_sha: str | None = None
        self.pr_state = "open"
        self.on_label_delete: Callable[[], None] | None = None
        self.on_audit: Callable[[], None] | None = None
        self.runs: list[dict[str, Any]] = []
        self.superseded = False
        self.refuse_audit = False
        self.refuse_issues = False
        self.refuse_audit_update = False
        self.refuse_retry = False
        self.comment_fetches: Counter[int] = Counter()
        self.next_comment_id = 1
        self.issue_mutations: Counter[int] = Counter()

    def evidence(self, job: dict[str, Any]) -> Evidence:
        if self.use_transport:
            return super().evidence(job)
        return (
            Evidence(self.annotation_text, self.evidence_text)
            if job.get("steps")
            else Evidence()
        )

    def log(self, job_id: int) -> str:
        return self.evidence_text

    def request(
        self, method: str, path: str, data: dict[str, Any] | None = None
    ) -> Any:
        if path == "graphql":
            assert data is not None
            if data["query"].startswith("query"):
                return {
                    "data": {
                        "node": {"isPinned": data["variables"]["id"] in self.pinned}
                    }
                }
            if "unpinIssue" in data["query"]:
                self.pinned.discard(data["variables"]["id"])
                return {
                    "data": {"unpinIssue": {"issue": {"id": data["variables"]["id"]}}}
                }
            if data["variables"]["id"] in self.pinned:
                raise RuntimeError("The issue is already pinned")
            self.pinned.add(data["variables"]["id"])
            return {"data": {"pinIssue": {"issue": {"id": data["variables"]["id"]}}}}
        path = path.removeprefix(f"repos/{self.repository}/")
        url = urlsplit(path)
        parts = url.path.split("/")
        if parts[0] == "pulls":
            return {
                "state": self.pr_state,
                "head": {"sha": self.pr_sha or self.run["head_sha"]},
            }
        if parts[0] == "check-runs":
            return self.annotations
        if parts == ["labels"]:
            if method == "GET":
                return [{"name": name} for name in self.repository_labels]
            assert data is not None
            self.repository_labels.add(data["name"])
            return None
        if parts[:2] == ["actions", "workflows"]:
            main_query = parse_qs(url.query)["branch"] == ["main"]
            selected = (
                self.main or (self.run if self.run["head_branch"] == "main" else None)
                if main_query
                else self.run
            )
            if main_query and self.main_by_workflow:
                selected = self.main_by_workflow.get(parts[2])
            if self.superseded and not main_query:
                selected = dict(self.run, id=999)
            candidates = [selected] if selected else []
            if main_query:
                if self.last_completed_main:
                    candidates.append(self.last_completed_main)
                if parse_qs(url.query).get("status") == ["completed"]:
                    candidates = [
                        run for run in candidates if run["status"] == "completed"
                    ]
            else:
                candidates = self.other_runs + candidates
            return {"workflow_runs": copy.deepcopy(candidates)}
        if parts[:2] == ["actions", "runs"]:
            if len(parts) == 2:
                query = parse_qs(url.query)
                start, end = query["created"][0].split("..")
                runs = [
                    run
                    for run in self.runs
                    if start <= run.get("created_at", run["updated_at"]) <= end
                ]
                page = int(query["page"][0])
                if page > 10:
                    raise RuntimeError("GitHub run search caps at 1,000")
                return {
                    "total_count": len(runs),
                    "workflow_runs": runs[(page - 1) * 100 : page * 100],
                }
            if method == "POST":
                if self.refuse_retry:
                    raise RuntimeError("Actions API is unavailable")
                self.rerun_scopes.append(parts[-1])
                latest_jobs = {
                    job["name"]: job
                    for attempt in range(1, self.run["run_attempt"] + 1)
                    for job in self.jobs.get(attempt, [])
                }
                self.retried.extend(
                    job["id"]
                    for job in latest_jobs.values()
                    if parts[-1] == "rerun"
                    or job["conclusion"] not in {"success", "skipped"}
                )
                return None
            attempt = int(parts[4])
            jobs = (
                self.main_jobs
                if any(
                    main and int(parts[2]) == main["id"]
                    for main in (self.main, self.last_completed_main)
                )
                else self.run_jobs.get(int(parts[2]), self.jobs.get(attempt, []))
            )
            return {"jobs": copy.deepcopy(jobs)}
        if parts[:2] == ["actions", "jobs"]:
            if self.refuse_retry:
                raise RuntimeError("Actions API is unavailable")
            if int(parts[2]) not in {
                job["id"] for job in self.jobs.get(self.run["run_attempt"], [])
            }:
                raise RuntimeError("Only jobs from the current attempt can be re-run")
            self.retried.append(int(parts[2]))
            return None
        if parts == ["issues"]:
            if self.refuse_issues:
                raise RuntimeError("Issue API is unavailable")
            if method == "GET":
                query = parse_qs(url.query)
                state = query.get("state", ["all"])[0]
                labels = set(query.get("labels", [""])[0].split(",")) - {""}
                creator = query.get("creator", [None])[0]
                issues = [
                    issue
                    for issue in reversed(self.issues)
                    if (state == "all" or issue["state"] == state)
                    and (creator is None or issue["user"]["login"] == creator)
                    and labels
                    <= {
                        label["name"] if isinstance(label, dict) else label
                        for label in issue.get("labels", [])
                    }
                ]
                return copy.deepcopy(issues)
            assert data is not None
            issue = {
                **data,
                "number": len(self.issues) + 1,
                "node_id": f"I_{len(self.issues) + 1}",
                "state": "open",
                "user": {"login": "github-actions[bot]"},
            }
            self.issues.append(issue)
            return copy.deepcopy(issue)
        if parts[:2] == ["issues", "comments"]:
            if self.refuse_audit_update:
                raise RuntimeError("Issue update API is unavailable")
            assert data is not None
            comment = next(
                item
                for comments in self.comments.values()
                for item in comments
                if item.get("id") == int(parts[2])
            )
            comment.update(data)
            return copy.deepcopy(comment)
        if parts[0] == "issues":
            number = int(parts[1])
            stored = next(
                (issue for issue in self.issues if issue["number"] == number), None
            )
            labels = (
                {
                    label["name"] if isinstance(label, dict) else label
                    for label in stored.get("labels", [])
                }
                if stored is not None
                else set(self.labels)
            )
            if parts[-1] == "labels":
                if method == "GET":
                    return [{"name": label} for label in labels]
                assert data is not None
                self.issue_mutations[number] += 1
                labels.update(data["labels"])
                if stored is not None:
                    stored["labels"] = sorted(labels)
                else:
                    self.labels.update(labels)
                return [{"name": label} for label in labels]
            if method == "DELETE":
                self.issue_mutations[number] += 1
                labels.discard(parts[-1])
                if stored is not None:
                    stored["labels"] = sorted(labels)
                else:
                    self.labels.discard(parts[-1])
                if self.on_label_delete:
                    callback = self.on_label_delete
                    self.on_label_delete = None
                    callback()
                return [{"name": label} for label in labels]
            if parts[-1] == "comments":
                comments = self.comments.setdefault(number, [])
                if method == "GET":
                    self.comment_fetches[number] += 1
                    query = parse_qs(url.query)
                    page = int(query.get("page", ["1"])[0])
                    return copy.deepcopy(comments[(page - 1) * 100 : page * 100])
                if self.refuse_audit:
                    raise RuntimeError("Issue API is unavailable")
                assert data is not None
                comment = {
                    **data,
                    "id": self.next_comment_id,
                    "user": {"login": "github-actions[bot]"},
                }
                self.next_comment_id += 1
                comments.append(comment)
                if self.on_audit and "Retry requested:" in data["body"]:
                    callback = self.on_audit
                    self.on_audit = None
                    callback()
                return copy.deepcopy(comment)
            assert data is not None
            self.issue_mutations[number] += 1
            issue = next(item for item in self.issues if item["number"] == number)
            issue.update(data)
            return copy.deepcopy(issue)
        raise AssertionError(f"Unrecorded API resource: {method} {path}")


def ledger_comments(api: ReplayGitHub) -> list[str]:
    ledger = next(
        issue
        for issue in reversed(api.issues)
        if LEDGER in issue["body"] and issue["state"] == "open"
    )
    return [comment["body"] for comment in api.comments.get(ledger["number"], [])]


def test_lost_before_first_step_is_retried_once_and_audited() -> None:
    api = ReplayGitHub()
    job = recorded("lost_runner")
    api.jobs[1] = [job]
    api.run["conclusion"] = "cancelled"
    Observer(api).process(api.run)
    assert api.retried == [job["id"]]
    record = ledger_comments(api)[0]
    for fact in (
        "lost runner",
        api.run["head_sha"],
        "before first step",
        "123",
        job["name"],
    ):
        assert fact in record
    assert api.pinned
    Observer(api).process(api.run)
    assert api.retried == [job["id"]]
    api.run["run_attempt"] = 2
    api.jobs[2] = [job]
    Observer(api).process(api.run)
    assert api.retried == [job["id"]]


def test_superseded_zero_step_cancellation_does_not_restart_old_head() -> None:
    api = ReplayGitHub()
    api.jobs[1] = [recorded("lost_runner")]
    api.run["conclusion"] = "cancelled"
    api.superseded = True
    Observer(api).process(api.run)
    assert api.retried == []
    assert api.issues == []


def test_lost_runner_after_a_step_is_also_retried() -> None:
    api = ReplayGitHub()
    api.annotation_text = "The hosted runner lost communication with the server"
    Observer(api).process(api.run)
    assert len(api.retried) == 1
    assert "lost runner" in ledger_comments(api)[0]


def test_multiple_lost_jobs_are_recovered_together_without_stale_job_ids() -> None:
    api = ReplayGitHub()
    first = recorded("lost_runner")
    second = dict(first, id=first["id"] + 1, name="second runner")
    api.jobs[1] = [first, second]
    api.run["conclusion"] = "cancelled"
    Observer(api).process(api.run)
    assert api.retried == [first["id"], second["id"]]
    assert api.rerun_scopes == ["rerun"]
    api.run.update(run_attempt=2, conclusion="success")
    api.jobs[2] = [
        dict(first, conclusion="success"),
        dict(second, conclusion="success"),
    ]
    Observer(api).process(api.run)
    assert len(api.retried) == 2


def test_deliberate_cancellation_after_steps_is_not_retried_by_landing() -> None:
    api = ReplayGitHub()
    api.jobs[1][0]["conclusion"] = "cancelled"
    api.evidence_text = "The operation was canceled."
    api.run["conclusion"] = "cancelled"
    Observer(api).process(api.run, rerun_tests=True)
    assert api.retried == []


def test_mirror_outage_has_a_finite_retry_budget() -> None:
    api = ReplayGitHub()
    api.annotation_text = "Upstream package mirror unreachable"
    api.evidence_text = ""
    for attempt in (1, 2, 3):
        api.run["run_attempt"] = attempt
        api.jobs[attempt] = [recorded("test_failure")]
        Observer(api).process(api.run)
    assert len(api.retried) == 2
    assert "mirror/network" in ledger_comments(api)[0]


def test_test_failure_is_not_an_infrastructure_flake() -> None:
    api = ReplayGitHub()
    api.evidence_text += "\nConnection reset by peer"
    Observer(api).process(api.run)
    assert api.retried == []
    Observer(api).process(api.run, rerun_tests=True)
    assert len(api.retried) == 1
    assert "test failure" in ledger_comments(api)[0]
    assert "AssertionError" in ledger_comments(api)[0]
    assert "Run Tests" in ledger_comments(api)[0]


def test_missing_audit_cannot_block_lost_runner_recovery(
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = ReplayGitHub()
    api.jobs[1] = [recorded("lost_runner")]
    api.run["conclusion"] = "cancelled"
    api.refuse_audit = True
    Observer(api).process(api.run)
    assert api.retried == [api.jobs[1][0]["id"]]
    assert "ci-audit-skipped" in capsys.readouterr().err


def passed_job() -> dict[str, Any]:
    job: dict[str, Any] = recorded("test_failure")
    job["conclusion"] = "success"
    for step in job["steps"]:
        if step["conclusion"] == "failure":
            step["conclusion"] = "success"
    return job


def test_same_sha_failed_then_passed_opens_one_flaky_bug_per_test_id() -> None:
    api = ReplayGitHub()
    api.run.update(run_attempt=2, conclusion="success")
    api.jobs[2] = [passed_job()]
    Observer(api).process(api.run)
    bugs = [issue for issue in api.issues if issue["title"].startswith("Flaky:")]
    assert len(bugs) == 1
    assert (
        "test_a_resumed_chunked_realign_keeps_chunks_when_the_gpu_comes_back"
        in bugs[0]["title"]
    )
    assert "bug" in bugs[0]["labels"]
    assert api.run["head_sha"] in bugs[0]["body"]
    api.run["id"] = 101
    Observer(api).process(api.run)
    assert len(api.issues) == 1
    assert len(api.comments[bugs[0]["number"]]) == 2


def test_skipped_test_step_is_not_evidence_a_test_passed() -> None:
    api = ReplayGitHub()
    api.run.update(run_attempt=2, conclusion="success")
    job = passed_job()
    for step in job["steps"]:
        if step["name"] == "Run Tests":
            step["conclusion"] = "skipped"
    api.jobs[2] = [job]
    Observer(api).process(api.run)
    assert api.issues == []


def test_parametrized_flake_ids_with_spaces_are_not_truncated_or_merged() -> None:
    api = ReplayGitHub()
    api.evidence_text = (
        "FAILED tests/test_cli.py::test_path[one path] - AssertionError\n"
        "FAILED tests/test_cli.py::test_path[another path] - AssertionError"
    )
    api.run.update(run_attempt=2, conclusion="success")
    api.jobs[2] = [passed_job()]
    Observer(api).process(api.run)
    titles = {issue["title"] for issue in api.issues}
    assert titles == {
        "Flaky: tests/test_cli.py::test_path[one path]",
        "Flaky: tests/test_cli.py::test_path[another path]",
    }


def test_recovery_after_superseding_push_still_reports_the_confirmed_flake() -> None:
    api = ReplayGitHub()
    api.run.update(run_attempt=2, conclusion="success")
    api.jobs[2] = [passed_job()]
    api.superseded = True
    Observer(api).process(api.run)
    assert len(api.issues) == 1
    assert api.issues[0]["title"].startswith("Flaky:")
    assert api.retried == []


def test_intermediate_runner_loss_cannot_hide_a_confirmed_test_flake() -> None:
    api = ReplayGitHub()
    api.jobs[2] = [dict(recorded("lost_runner"), name="test-macos")]
    api.jobs[3] = [passed_job()]
    api.run.update(run_attempt=3, conclusion="success")
    Observer(api).process(api.run)
    assert any(issue["title"].startswith("Flaky:") for issue in api.issues)


def test_red_main_opens_updates_and_closes_a_bug_with_test_and_commit() -> None:
    api = ReplayGitHub()
    api.run.update(head_branch="main", event="push", pull_requests=[])
    Observer(api).process(api.run)
    issue = api.issues[0]
    assert issue["title"].startswith("main is red:")
    assert "bug" in issue["labels"]
    assert api.run["head_sha"] in issue["body"]
    assert "test_a_resumed_chunked_realign" in issue["body"]
    api.run["id"] = 101
    Observer(api).process(api.run)
    assert len(api.issues) == 1
    api.run["conclusion"] = "success"
    Observer(api).process(api.run)
    assert issue["state"] == "closed"


def test_pr_matching_red_main_is_labelled_and_never_rerun_even_with_landing_override() -> (
    None
):
    api = ReplayGitHub()
    api.main = dict(api.run, id=200, head_branch="main", event="push")
    api.main_jobs = [recorded("test_failure")]
    assert Observer(api).process(api.run, rerun_tests=True) == 2
    assert api.retried == []
    assert "ci-red-main" in api.labels


@pytest.mark.parametrize(
    ("prefix", "job_name", "workflow", "workflow_name"),
    [
        ("macos", "test-macos", "macos_ci.yml", "CI macOS"),
        pytest.param(
            "smoke",
            "smoke (ubuntu-latest, package)",
            "cli_smoke.yml",
            "CLI package and benchmark smoke",
            id="761-parallel-smoke",
        ),
    ],
)
def test_routed_pr_job_compares_reusable_main_not_pr_only_wrapper(
    prefix: str, job_name: str, workflow: str, workflow_name: str
) -> None:
    api = ReplayGitHub()
    api.run["name"] = "PR area checks"
    api.jobs[1][0]["name"] = f"{prefix} / {job_name}"
    if prefix == "smoke":
        api.annotation_text = ""
        api.evidence_text = "error: wheel install failed"
        for step in api.jobs[1][0]["steps"]:
            if step["conclusion"] == "failure":
                step["name"] = "Build and Smoke Test Package"
    api.main = dict(
        api.run, id=200, name=workflow_name, head_branch="main", event="push"
    )
    api.main_by_workflow[workflow] = api.main
    api.main_jobs = copy.deepcopy(api.jobs[1])
    api.main_jobs[0]["name"] = job_name
    assert Observer(api).process(api.run, rerun_tests=True) == 2
    assert api.retried == []
    assert "ci-red-main" in api.labels


def test_queued_main_fix_does_not_release_existing_main_failure_quarantine() -> None:
    api = ReplayGitHub()
    api.main = dict(
        api.run,
        id=201,
        head_branch="main",
        event="push",
        status="queued",
        conclusion=None,
    )
    api.last_completed_main = dict(
        api.main, id=200, status="completed", conclusion="failure"
    )
    api.main_jobs = [recorded("test_failure")]
    assert Observer(api).process(api.run, rerun_tests=True) == 2
    assert api.retried == []


def test_different_pr_failure_is_not_mistaken_for_red_main() -> None:
    api = ReplayGitHub()
    api.main = dict(api.run, id=200, head_branch="main", event="push")
    api.main_jobs = [recorded("lost_runner")]
    api.evidence_text = ""
    Observer(api).process(api.run, rerun_tests=True)
    assert len(api.retried) == 1
    assert "ci-red-main" not in api.labels


def test_confirmed_flake_on_main_is_not_quarantined_as_a_main_regression() -> None:
    api = ReplayGitHub()
    api.run.update(run_attempt=2, conclusion="success")
    api.jobs[2] = [passed_job()]
    Observer(api).process(api.run)
    api.run.update(id=102, run_attempt=1, conclusion="failure")
    api.main = dict(api.run, id=200, head_branch="main", event="push")
    api.main_jobs = [recorded("test_failure")]
    Observer(api).process(api.run, rerun_tests=True)
    assert len(api.retried) == 1
    assert "ci-red-main" not in api.labels


def test_main_green_removes_obsolete_pr_quarantine_label() -> None:
    api = ReplayGitHub()
    api.run["conclusion"] = "success"
    api.labels.add("ci-red-main")
    Observer(api).process(api.run)
    assert "ci-red-main" not in api.labels


def test_unrelated_success_does_not_remove_another_workflows_quarantine() -> None:
    api = ReplayGitHub()
    api.main = dict(api.run, id=200, head_branch="main", event="push")
    api.main_jobs = [recorded("test_failure")]
    Observer(api).process(api.run)
    assert "ci-red-main" in api.labels
    api.run.update(workflow_id=api.run["workflow_id"] + 1, conclusion="success")
    Observer(api).process(api.run)
    assert "ci-red-main" in api.labels


def test_new_head_invalidates_old_quarantine_without_observing_successful_first_attempts() -> (
    None
):
    api = ReplayGitHub()
    api.main = dict(api.run, id=200, head_branch="main", event="push")
    api.main_jobs = [recorded("test_failure")]
    Observer(api).process(api.run)
    assert "ci-red-main" in api.labels
    api.pr_sha = "a" * 40
    Quarantine(api).reset(123)
    assert "ci-red-main" not in api.labels


def test_weekly_ledger_reports_run_level_retry_rate() -> None:
    api = ReplayGitHub()
    api.runs = [
        dict(api.run, id=100 + index, run_attempt=attempt)
        for index, attempt in enumerate((1, 1, 1, 2))
    ]
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    Observer(api).rollup(now=now)
    assert "25.0%" in ledger_comments(api)[0]
    assert api.run["name"] in ledger_comments(api)[0]
    Observer(api).rollup(now=now)
    assert len(ledger_comments(api)) == 1


def test_log_content_cannot_mention_users_or_inject_issue_html() -> None:
    api = ReplayGitHub()
    api.annotation_text = "Upstream package mirror unreachable"
    api.evidence_text = (
        "Upstream package mirror unreachable\nerror @theontho <script>x</script>"
    )
    Observer(api).process(api.run)
    body = ledger_comments(api)[0]
    assert "@theontho" not in body
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
