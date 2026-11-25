"""A failed or mismatched dispatch must never authorize a public release."""

from __future__ import annotations

import re
import time
from typing import Any

import pytest

from scripts import ci_release_gate as gate
from tests.test_github_actions_policy import (
    WORKFLOWS,
    event_path_filters,
    matches_workflow_condition,
)

REPO = "WGSExtract/wgsextract-cli"
TAG = "v1.2.3"
SHA = "a" * 40


@pytest.mark.parametrize("name", [*gate.BUILD_GATES, gate.GUI, gate.LAB])
def test_release_gates_never_start_on_pr_queue_push_or_schedule(name: str) -> None:
    workflow = WORKFLOWS / name
    assert event_path_filters(workflow, "workflow_dispatch") is not None
    for event in ("pull_request", "merge_group", "push", "schedule", "workflow_call"):
        assert event_path_filters(workflow, event) is None
    assert (
        f"uses: ./.github/workflows/{name}" not in (WORKFLOWS / "pr_ci.yml").read_text()
    )


@pytest.mark.parametrize("name", gate.FULL_OS_GATES)
def test_release_os_lanes_can_dispatch_but_do_not_run_on_main_push(name: str) -> None:
    workflow = WORKFLOWS / name
    assert event_path_filters(workflow, "workflow_dispatch") is not None
    assert event_path_filters(workflow, "schedule") is not None
    assert event_path_filters(workflow, "push") is None


@pytest.mark.parametrize(
    ("preflight_result", "cancelled", "allowed"),
    [
        ("success", False, True),
        ("failure", False, False),
        ("skipped", False, False),
        ("success", True, False),
    ],
)
def test_existing_tag_can_publish_only_after_green_preflight(
    preflight_result: str, cancelled: bool, allowed: bool
) -> None:
    source = (WORKFLOWS / "release.yml").read_text()
    preflight, publish = source.split("\n  preflight:\n", 1)[1].split(
        "\n  publish:\n", 1
    )
    assert "actions: write" in preflight and "contents: write" not in preflight
    assert "needs: [resolve, preflight]" in publish
    condition = re.search(r"^    if: \$\{\{ (.+) \}\}", publish, re.M)
    assert condition is not None
    assert (
        matches_workflow_condition(
            condition[1],
            {
                "cancelled()": cancelled,
                "needs.resolve.result": "success",
                "needs.preflight.result": preflight_result,
            },
        )
        is allowed
    )
    assert "gh release create" not in source.split("\n  publish:\n", 1)[0]
    assert "artifact-ids: ${{ needs.preflight.outputs.gui_artifact }}" in publish
    assert event_path_filters(WORKFLOWS / "release.yml", "push") == {
        "branches": ["main"],
        "paths": ["pyproject.toml"],
    }


class PreflightAPI:
    def __init__(self, bad_workflow: str, bad_run: dict[str, Any]) -> None:
        self.bad_workflow = bad_workflow
        self.bad_run = bad_run
        self.runs: dict[int, dict[str, Any]] = {}
        self.app_input: str | None = None
        self.gui_completed = False
        self.lab_checked = False

    def request(
        self, endpoint: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if payload is None:
            if endpoint.endswith("/artifacts?name=gui-release-assets"):
                return {
                    "artifacts": [
                        {"id": 42, "name": "gui-release-assets", "expired": False}
                    ]
                }
            run = self.runs[int(endpoint.rsplit("/", 1)[1])]
            if run["path"].endswith(f"/{gate.GUI}"):
                self.gui_completed = True
            if run["path"].endswith(f"/{gate.LAB}"):
                self.lab_checked = True
            return run
        assert payload["ref"] == TAG
        workflow = endpoint.split("/")[-2]
        inputs = payload["inputs"]
        run_id = len(self.runs) + 1
        if workflow == gate.GUI:
            assert inputs == {"tag": TAG, "candidate": True}
            self.app_input = str(run_id)
        elif workflow == gate.LAB:
            assert self.gui_completed
            assert inputs == {"app_run": self.app_input, "candidate": True}
        else:
            assert not inputs  # native builds must not publish tool releases
        run = {
            "id": run_id,
            "path": f".github/workflows/{workflow}",
            "head_repository": {"full_name": REPO},
            "head_sha": SHA,
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
        }
        if workflow == self.bad_workflow:
            run.update(self.bad_run)
        self.runs[run_id] = run
        return {"workflow_run_id": run_id}


def test_candidate_requires_gui_artifacts_and_full_install_lab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = PreflightAPI("", {})
    monkeypatch.setattr(gate, "api", api.request)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    gui_run, artifact = gate.preflight(REPO, TAG, SHA)
    assert (str(gui_run), artifact) == (api.app_input, 42)
    assert api.lab_checked
    assert len(api.runs) == len(gate.FULL_OS_GATES) + len(gate.BUILD_GATES) + 2


@pytest.mark.parametrize(
    ("workflow", "bad_run", "error"),
    [
        (gate.GUI, {"head_sha": "b" * 40}, "not a dispatch"),
        (gate.GUI, {"path": ".github/workflows/unrelated.yml"}, "not a dispatch"),
        (gate.GUI, {"conclusion": "cancelled"}, "concluded cancelled"),
        (gate.LAB, {"conclusion": "failure"}, "concluded failure"),
        ("windows_ci.yml", {"conclusion": "failure"}, "concluded failure"),
    ],
)
def test_failed_or_foreign_run_refuses_to_complete_preflight(
    monkeypatch: pytest.MonkeyPatch, workflow: str, bad_run: dict[str, Any], error: str
) -> None:
    api = PreflightAPI(workflow, bad_run)
    monkeypatch.setattr(gate, "api", api.request)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match=error):
        gate.preflight(REPO, TAG, SHA)
    assert api.lab_checked is (workflow == gate.LAB)


def test_gui_rerun_during_install_lab_cannot_change_the_validated_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = PreflightAPI("", {})

    def request(endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = api.request(endpoint, payload)
        if api.lab_checked and response.get("path", "").endswith(f"/{gate.GUI}"):
            return {**response, "run_attempt": 2}
        return response

    monkeypatch.setattr(gate, "api", request)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="changed during preflight"):
        gate.preflight(REPO, TAG, SHA)
