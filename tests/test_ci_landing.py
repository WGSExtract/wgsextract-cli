"""Focused ci landing regressions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import pr_checks_ready
from scripts.pr_checks_ready import checks_ready, latest_ready_run
from tests.test_ci_changes import (
    LINUX_BASH as LINUX_BASH,
)
from tests.test_ci_changes import (
    area_run as area_run,
)
from tests.test_ci_changes import (
    workflow_shell as workflow_shell,
)


@pytest.mark.parametrize(
    ("buckets", "ready"),
    [
        ([], False),
        (["skipping"], False),
        (["pass"], True),
        (["pass", "skipping"], True),
        (["pass", "pending"], False),
        (["pass", "fail"], False),
        (["pass", "cancel"], False),
        (["pass", "unknown"], False),
    ],
)
def test_757_landing_accepts_one_green_check_not_a_fixed_matrix_size(
    buckets: list[str], ready: bool
) -> None:
    checks = [
        {
            "name": "required",
            "bucket": buckets[0] if buckets else "pending",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/10/job/100",
        },
        {
            "name": "Draft lint and fast tests",
            "bucket": "skipping",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/10/job/101",
        },
        {
            "name": "changes",
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/10/job/102",
        },
        {
            "name": "Ready PR area checks",
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/10/job/103",
        },
        *[{"bucket": bucket} for bucket in buckets[1:]],
    ]
    assert checks_ready(checks, run_id=10) is ready


def test_802_old_draft_passes_and_skips_cannot_land_before_ready_ci_registers() -> None:
    checks = [
        {
            "name": "Draft lint and fast tests",
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/10/job/100",
        },
        *[
            {"name": name, "bucket": "skipping"}
            for name in ("ubuntu", "macos", "windows")
        ],
        *[{"name": name, "bucket": "pass"} for name in ("changes", "lint", "docs")],
    ]
    assert not checks_ready(checks, run_id=10)


@pytest.mark.parametrize(
    ("gate_bucket", "draft_bucket", "routing_run", "ready"),
    [
        ("pending", "skipping", 11, False),
        ("pass", "pass", 11, False),
        ("pass", "skipping", 10, False),
        ("pass", "skipping", 11, True),
    ],
)
def test_802_landing_requires_completed_non_draft_validation_from_one_run(
    gate_bucket: str, draft_bucket: str, routing_run: int, ready: bool
) -> None:
    checks = [
        {
            "name": "Ready PR area checks",
            "bucket": gate_bucket,
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/11/job/110",
        },
        {
            "name": "Draft lint and fast tests",
            "bucket": draft_bucket,
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/11/job/111",
        },
        {
            "name": "changes",
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/{routing_run}/job/112",
        },
        {
            "name": "required",
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/11/job/113",
        },
    ]
    assert checks_ready(checks, run_id=11) is ready


def test_910_readiness_validates_without_any_preceding_draft_run() -> None:
    assert (
        latest_ready_run(
            [],
            head_sha="same-head",
            ready_since="2026-11-13T16:20:00Z",
            pr_number=802,
        )
        is None
    )
    assert (
        latest_ready_run(
            [area_run(13, "2026-11-13T16:20:02Z")],
            head_sha="same-head",
            ready_since="2026-11-13T16:20:00Z",
            pr_number=802,
        )
        == 13
    )


@pytest.mark.parametrize("new_run_registered", [False, True])
def test_802_redrafting_the_same_head_cannot_reuse_validation_before_latest_readiness(
    new_run_registered: bool,
) -> None:
    runs = [
        area_run(10, "2026-11-13T16:00:00Z"),
        area_run(11, "2026-11-13T16:10:00Z", action="labeled", draft=True),
        area_run(12, "2026-11-13T16:20:01Z", action="labeled"),
    ]
    if new_run_registered:
        runs.append(area_run(13, "2026-11-13T16:20:02Z"))
    expected_run = latest_ready_run(
        runs,
        head_sha="same-head",
        ready_since="2026-11-13T16:20:00Z",
        pr_number=802,
    )
    assert expected_run == (13 if new_run_registered else None)
    old_checks = [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/10/job/{job}",
        }
        for job, name in enumerate(("changes", "required", "Ready PR area checks"), 100)
    ]
    assert not checks_ready(old_checks, run_id=expected_run)
    if new_run_registered:
        fresh_checks = [
            {**check, "link": check["link"].replace("/runs/10/", "/runs/13/")}
            for check in old_checks
        ]
        assert checks_ready(fresh_checks, run_id=expected_run)


@pytest.mark.parametrize("status", ["queued", "in_progress", "completed"])
def test_802_newest_ready_run_must_finish_without_falling_back_to_an_older_success(
    status: str,
) -> None:
    unrelated = area_run(22, "2026-11-13T16:21:02Z")
    unrelated["head_sha"] = "another-head"
    other_pr = area_run(23, "2026-11-13T16:21:03Z")
    other_pr["pull_requests"] = [{"number": 999}]
    queue_run = area_run(24, "2026-11-13T16:21:04Z")
    queue_run["event"] = "merge_group"
    queue_run["display_title"] = "PR area checks / checks_requested / queue"
    runs = [
        area_run(20, "2026-11-13T16:20:01Z"),
        area_run(21, "2026-11-13T16:21:01Z", action="synchronize", status=status),
        unrelated,
        other_pr,
        queue_run,
    ]
    assert latest_ready_run(
        runs,
        head_sha="same-head",
        ready_since="2026-11-13T16:20:00Z",
        pr_number=802,
    ) == (21 if status == "completed" else None)


@pytest.mark.parametrize("redrafted_during_discovery", [False, True])
def test_802_ready_transition_changes_during_validation_fail_closed(
    monkeypatch: pytest.MonkeyPatch, redrafted_during_discovery: bool
) -> None:
    snapshot = {
        "headRefOid": "same-head",
        "isDraft": False,
        "createdAt": "2026-11-13T16:00:00Z",
        "timelineItems": {
            "nodes": [{"id": "ready-1", "createdAt": "2026-11-13T16:20:00Z"}]
        },
    }
    after = {
        **snapshot,
        "timelineItems": {
            "nodes": [{"id": "ready-2", "createdAt": "2026-11-13T16:30:00Z"}]
        },
    }
    snapshots = iter([snapshot, after if redrafted_during_discovery else snapshot])

    def github_response(*arguments: str) -> Any:
        if arguments[:2] == ("api", "graphql"):
            return {"data": {"repository": {"pullRequest": next(snapshots)}}}
        return [{"workflow_runs": [area_run(20, "2026-11-13T16:20:01Z")]}]

    monkeypatch.setattr(pr_checks_ready, "github_json", github_response)
    assert pr_checks_ready.current_ready_run(802, "owner/repo") == (
        (None, [])
        if redrafted_during_discovery
        else (20, [area_run(20, "2026-11-13T16:20:01Z")])
    )


def test_757_landing_cli_rejects_an_empty_check_report() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "pr_checks_ready.py"
    result = subprocess.run(
        [sys.executable, str(script), "--pr", "802"],
        input=json.dumps([]),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


@pytest.mark.parametrize("latest_conclusion", ["success", "failure", "cancelled", None])
@pytest.mark.parametrize("reported", [False, True])
def test_876_superseded_workflow_checks_do_not_hide_the_latest_result(
    latest_conclusion: str | None,
    reported: bool,
) -> None:
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/ci_failures/cancelled_draft_gate.json"
        ).read_text()
    )
    draft = fixture["run"]
    ready = {
        **draft,
        "id": draft["id"] + 1,
        "display_title": "PR area checks / ready_for_review / ready",
        "conclusion": "success",
        "pull_requests": [{"number": 875}],
        "created_at": "2026-11-14T09:20:00Z",
    }
    scan = {
        **draft,
        "id": draft["id"] + 2,
        "workflow_id": 123,
        "path": ".github/workflows/gitleaks.yml",
    }
    replacement = {
        **scan,
        "id": scan["id"] + 1,
        "conclusion": latest_conclusion,
        "status": "completed" if latest_conclusion else "queued",
    }
    buckets = {"success": "pass", "failure": "fail", "cancelled": "cancel"}
    checks = [
        {
            "name": job["name"],
            "bucket": buckets[job["conclusion"]],
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/{draft['id']}/job/{i}",
        }
        for i, job in enumerate(fixture["jobs"])
    ]
    checks += [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/{ready['id']}/job/{i}",
        }
        for i, name in enumerate(("changes", "required", "Ready PR area checks"), 20)
    ]
    checks.append(
        {
            "name": "gitleaks",
            "bucket": "cancel",
            "workflow": "Secret Scan",
            "link": f"https://github.com/owner/repo/actions/runs/{scan['id']}/job/30",
        }
    )
    # Queued replacement deliberately has no registered check yet.
    if latest_conclusion:
        checks.append(
            {
                **checks[-1],
                "bucket": buckets[latest_conclusion],
                "link": f"https://github.com/owner/repo/actions/runs/{replacement['id']}/job/31",
            }
        )
    if not reported:
        checks = [check for check in checks if check["workflow"] == "PR area checks"]
    assert checks_ready(
        checks, run_id=ready["id"], runs=[replacement, draft, scan, ready]
    ) is (not reported or latest_conclusion == "success")
    assert checks_ready(checks, run_id=ready["id"], runs=[draft, ready]) is (
        not reported
    )


@pytest.mark.parametrize(
    "link",
    [
        "",
        "https://external.example/check/30",
        "https://github.com/owner/repo/actions/runs/not-a-run/job/30",
        "https://github.com/owner/repo/actions/runs/999/job/30",
    ],
)
def test_876_unverifiable_passing_check_cannot_establish_readiness(link: str) -> None:
    ready = {**area_run(20, "2026-11-13T16:20:01Z"), "workflow_id": 416978936}
    checks = [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/20/job/{i}",
        }
        for i, name in enumerate(("changes", "required", "Ready PR area checks"))
    ]
    checks.append(
        {"name": "unverified", "bucket": "pass", "workflow": "Other", "link": link}
    )
    assert not checks_ready(checks, run_id=20, runs=[ready])


@pytest.mark.parametrize(
    ("bucket", "changes", "ready"),
    [
        ("pass", {}, True),
        ("pass", {"app": {"slug": "some-other-app"}}, False),
        ("pass", {"head_sha": "older-head"}, False),
        ("pass", {"status": "in_progress", "conclusion": None}, False),
        ("fail", {"conclusion": "failure"}, False),
        ("pass", {"conclusion": "failure"}, False),
        (
            "pass",
            {"html_url": "https://github.com/other/repo/runs/132889584422"},
            False,
        ),
    ],
)
def test_962_codeql_app_check_run_is_verified_against_the_pr_head(
    monkeypatch: pytest.MonkeyPatch,
    bucket: str,
    changes: dict[str, Any],
    ready: bool,
) -> None:
    repository = "WGSExtract/wgsextract-cli"
    link = f"https://github.com/{repository}/runs/132889584422"
    # Shape of PR #956's code-scanning check run: an app check, not an Actions job.
    codeql = {
        "id": 132889584422,
        "name": "CodeQL",
        "head_sha": "same-head",
        "status": "completed",
        "conclusion": "success",
        "html_url": link,
        "app": {"slug": "github-advanced-security"},
        **changes,
    }

    def response(*arguments: str) -> Any:
        assert arguments == ("api", f"repos/{repository}/check-runs/132889584422")
        return codeql

    monkeypatch.setattr(pr_checks_ready, "github_json", response)
    ready_run = {**area_run(20, "2026-11-13T16:20:01Z"), "workflow_id": 416978936}
    checks = [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/{repository}/actions/runs/20/job/{i}",
        }
        for i, name in enumerate(("changes", "required", "Ready PR area checks"))
    ]
    checks.append({"name": "CodeQL", "bucket": bucket, "workflow": "", "link": link})
    check_runs = pr_checks_ready.app_check_runs(checks, repository)
    assert (
        checks_ready(
            checks,
            run_id=20,
            runs=[ready_run],
            check_runs=check_runs,
            repository=repository,
        )
        is ready
    )
    # An app check under another repository is never trusted.
    assert not checks_ready(
        [*checks[:-1], {**checks[-1], "link": "https://github.com/a/b/runs/1"}],
        run_id=20,
        runs=[ready_run],
        check_runs=check_runs,
        repository=repository,
    )


def test_876_recorded_pr_run_with_empty_associations_is_still_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/ci_failures/cancelled_draft_gate.json"
        ).read_text()
    )
    run = {
        **fixture["run"],
        "display_title": "PR area checks / ready_for_review / ready",
        "conclusion": "success",
    }
    pr = {
        "headRefOid": run["head_sha"],
        "headRefName": run["head_branch"],
        "headRepository": {"nameWithOwner": run["head_repository"]["full_name"]},
        "isDraft": False,
        "createdAt": run["created_at"],
        "timelineItems": {"nodes": []},
    }

    def response(*arguments: str) -> Any:
        if arguments[:2] == ("api", "graphql"):
            return {"data": {"repository": {"pullRequest": pr}}}
        return [{"workflow_runs": [run]}]

    monkeypatch.setattr(pr_checks_ready, "github_json", response)
    assert pr_checks_ready.current_ready_run(875, "WGSExtract/wgsextract-cli") == (
        run["id"],
        [run],
    )


@LINUX_BASH
@pytest.mark.parametrize("dependency_result", ["failure", "cancelled"])
def test_876_active_run_cannot_pass_with_a_failed_or_timed_out_dependency(
    dependency_result: str,
) -> None:
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/ci_failures/cancelled_draft_gate.json"
        ).read_text()
    )
    needs = {
        job["name"]: {"result": "success"}
        for job in fixture["jobs"]
        if job["name"] != "required"
    }
    needs["gitleaks / gitleaks"]["result"] = dependency_result
    result = subprocess.run(
        [
            "bash",
            "-c",
            workflow_shell("Require routing and every selected required job to pass"),
        ],
        env={
            **os.environ,
            "NEEDS": json.dumps(needs),
            "ROUTED": "true",
            "EVENT_NAME": "merge_group",
            "QUEUE_RUNNERS": "[]",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "gitleaks / gitleaks" in result.stderr


def test_867_ready_run_found_by_workflow_file_not_run_name() -> None:
    """#867: the Actions API reports pr_ci.yml's run-name as `name`, so every
    PR was rejected. The ready run is found by its workflow file, and a run of
    another workflow with the same title is not taken for it."""
    ready = area_run(30, "2026-11-13T16:21:00Z", action="synchronize")
    other_workflow = area_run(31, "2026-11-13T16:22:00Z", action="synchronize")
    other_workflow["path"] = ".github/workflows/ubuntu_ci.yml"
    found = pr_checks_ready.latest_ready_run(
        [ready, other_workflow],
        head_sha="same-head",
        ready_since="2026-11-13T16:20:00Z",
        pr_number=802,
    )
    assert found == 30
