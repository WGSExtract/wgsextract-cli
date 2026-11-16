"""A nightly may reuse only exact successful main push evidence (#1045)."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.ci_nightly import already_passed


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({}, True),
        ({"head_sha": "older"}, False),
        ({"event": "pull_request"}, False),
        ({"event": "schedule"}, False),
        ({"conclusion": "failure"}, False),
        ({"conclusion": "cancelled"}, False),
        ({"head_branch": "feature"}, False),
        ({"head_repository": {"full_name": "someone/fork"}}, False),
    ],
)
def test_1045_nightly_requires_conclusive_exact_main_success(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, Any], expected: bool
) -> None:
    run: dict[str, Any] = {
        "head_sha": "current",
        "head_branch": "main",
        "event": "push",
        "conclusion": "success",
        "head_repository": {"full_name": "WGSExtract/wgsextract-cli"},
    }
    run.update(change)

    def api(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, json.dumps({"workflow_runs": [run]}))

    monkeypatch.setattr(subprocess, "run", api)
    assert (
        already_passed("WGSExtract/wgsextract-cli", "windows_ci.yml", "current")
        is expected
    )


def test_1045_nightly_api_outage_runs_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    def api(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, ["gh"])

    monkeypatch.setattr(subprocess, "run", api)
    assert not already_passed("WGSExtract/wgsextract-cli", "windows_ci.yml", "current")
