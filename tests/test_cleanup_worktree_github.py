"""Reject unusable GitHub metadata before considering a worktree for cleanup."""

import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from tests.test_cleanup_worktrees import cleanup, manifest, topic
from tests.test_cleanup_worktrees import proc as proc
from tests.test_cleanup_worktrees import repo as repo

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process guards")


@pytest.mark.parametrize(
    ("page", "reason"),
    [
        ({"errors": [{"message": "private upstream error text"}]}, "reported errors"),
        ({"data": {"viewer": None}}, "authenticated GitHub account"),
        (None, "malformed page"),
    ],
    ids=["graphql-errors", "null-viewer", "not-an-object"],
)
def test_1393_unusable_first_github_page_keeps_worktree_and_explains_why(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page: object,
    reason: str,
) -> None:
    path = topic(repo)
    gh = tmp_path / "gh"
    gh.write_text(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(json.dumps([page]))}\n")
    gh.chmod(0o755)
    monkeypatch.setenv(
        "PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", os.defpath)
    )

    run = cleanup(repo, "--apply", "--github")
    assert run.returncode == 1
    assert reason in str(manifest(repo)["error"])
    assert "private upstream error text" not in run.stderr
    assert (path / "source.txt").is_file()
