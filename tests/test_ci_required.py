"""Focused ci required regressions."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from scripts.pr_checks_ready import checks_ready
from tests.test_ci_changes import (
    LINUX_BASH as LINUX_BASH,
)
from tests.test_ci_changes import (
    workflow_shell as workflow_shell,
)


@LINUX_BASH
@pytest.mark.parametrize(
    ("routing_result", "routed", "failed", "cancelled", "ready"),
    [
        ("success", "", "false", "false", False),
        ("failure", "true", "true", "false", False),
        ("success", "true", "true", "false", False),
        ("success", "true", "false", "true", False),
        ("success", "true", "false", "false", True),
    ],
)
def test_802_ready_gate_fails_for_missing_routing_or_failed_selected_checks(
    routing_result: str, routed: str, failed: str, cancelled: str, ready: bool
) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            workflow_shell("Require complete routing and successful selected checks"),
        ],
        env={
            **os.environ,
            "ROUTING_RESULT": routing_result,
            "ROUTED": routed,
            "CHECKS_FAILED": failed,
            "CHECKS_CANCELLED": cancelled,
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is ready


@LINUX_BASH
@pytest.mark.parametrize(
    ("routing_result", "routed", "ubuntu_result", "ready"),
    [
        ("success", "true", "success", True),
        ("success", "", "success", False),
        ("failure", "true", "success", False),
        ("success", "true", "failure", False),
        ("success", "true", "cancelled", False),
    ],
)
def test_802_queue_required_summary_accepts_its_small_matrix_but_not_missing_routing(
    routing_result: str, routed: str, ubuntu_result: str, ready: bool
) -> None:
    needs = {
        "changes": {"result": routing_result},
        "ubuntu": {"result": ubuntu_result},
        "gitleaks": {"result": "success"},
        "ready-gate": {"result": "skipped"},
        **{
            job: {"result": "skipped"}
            for job in ("draft-fast", "docs", "python-lint", "native-smoke", "pages")
        },
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            workflow_shell("Require routing and every selected required job to pass"),
        ],
        env={
            **os.environ,
            "NEEDS": json.dumps(needs),
            "ROUTED": routed,
            "EVENT_NAME": "merge_group",
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is ready


@LINUX_BASH
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "success"])
def test_1045_queue_required_rejects_failed_windows(conclusion: str) -> None:
    needs = {
        "changes": {"result": "success"},
        "ubuntu": {"result": "success"},
        "windows": {"result": conclusion},
        "ready-gate": {"result": "skipped"},
    }
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
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is (conclusion == "success")


@LINUX_BASH
@pytest.mark.parametrize(
    ("windows_result", "ready"),
    [("failure", False), ("cancelled", False), ("success", True), ("skipped", True)],
)
def test_949_required_consumes_area_verdict_for_windows_failure_or_certified_skip(
    windows_result: str, ready: bool
) -> None:
    area = subprocess.run(
        [
            "bash",
            "-c",
            workflow_shell("Require complete routing and successful selected checks"),
        ],
        env={
            **os.environ,
            "ROUTING_RESULT": "success",
            "ROUTED": "true",
            "CHECKS_FAILED": str(windows_result == "failure").lower(),
            "CHECKS_CANCELLED": str(windows_result == "cancelled").lower(),
        },
        capture_output=True,
        text=True,
    )
    needs = {
        "changes": {"result": "success"},
        "ubuntu": {"result": "success"},
        "gitleaks": {"result": "success"},
        "ready-gate": {"result": "success" if area.returncode == 0 else "failure"},
    }
    required = subprocess.run(
        [
            "bash",
            "-c",
            workflow_shell("Require routing and every selected required job to pass"),
        ],
        env={
            **os.environ,
            "NEEDS": json.dumps(needs),
            "ROUTED": "true",
            "EVENT_NAME": "pull_request",
        },
        capture_output=True,
        text=True,
    )
    assert (required.returncode == 0) is ready
    if not ready:
        assert "ready-gate" in required.stderr


@LINUX_BASH
def test_949_pr_required_rejects_a_skipped_area_gate() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            workflow_shell("Require routing and every selected required job to pass"),
        ],
        env={
            **os.environ,
            "NEEDS": json.dumps(
                {
                    "changes": {"result": "success"},
                    "ready-gate": {"result": "skipped"},
                }
            ),
            "ROUTED": "true",
            "EVENT_NAME": "pull_request",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ready-gate" in result.stderr


def test_949_landing_uses_required_instead_of_a_separate_area_gate_check() -> None:
    checks = [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/10/job/{job}",
        }
        for job, name in enumerate(("changes", "required"), 100)
    ]
    assert checks_ready(checks, run_id=10)


@pytest.mark.parametrize("required_bucket", [None, "pending", "skipping", "pass"])
def test_802_enqueue_requires_the_passing_required_check_from_the_current_pr_run(
    required_bucket: str | None,
) -> None:
    checks = [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/10/job/{job}",
        }
        for job, name in enumerate(("changes", "Ready PR area checks"), 100)
    ]
    if required_bucket:
        checks.append(
            {
                "name": "required",
                "bucket": required_bucket,
                "workflow": "PR area checks",
                "link": "https://github.com/owner/repo/actions/runs/10/job/102",
            }
        )
    assert checks_ready(checks, run_id=10) is (required_bucket == "pass")


@pytest.mark.parametrize(
    ("workflow", "failed_run", "bucket", "ready"),
    [
        ("PR area checks", 10, "cancel", True),
        ("PR area checks", 10, "fail", True),
        ("PR area checks", 11, "cancel", False),
        ("PR area checks", 11, "fail", False),
        ("Secret Scan", 10, "cancel", False),
        ("Secret Scan", 10, "fail", False),
    ],
)
def test_802_cancelled_draft_checks_do_not_poison_the_selected_ready_run(
    workflow: str, failed_run: int, bucket: str, ready: bool
) -> None:
    checks = [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/11/job/{job}",
        }
        for job, name in enumerate(("changes", "required", "Ready PR area checks"), 110)
    ]
    checks.extend(
        [
            {
                "name": "Draft lint and fast tests${{ label suffix }}",
                "bucket": "skipping",
                "workflow": "PR area checks",
                "link": "https://github.com/owner/repo/actions/runs/11/job/113",
            },
            {
                "name": "Draft lint and fast tests",
                "bucket": bucket,
                "workflow": workflow,
                "link": f"https://github.com/owner/repo/actions/runs/{failed_run}/job/100",
            },
        ]
    )
    assert checks_ready(checks, run_id=11) is ready
