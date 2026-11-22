"""Exercise cleanup while another checkout's generated directories change (#1393)."""

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.test_cleanup_worktrees import (
    cleanup,
    git,
    manifest,
    pull,
    result,
    topic,
)
from tests.test_cleanup_worktrees import (
    github as github,
)
from tests.test_cleanup_worktrees import (
    proc as proc,
)
from tests.test_cleanup_worktrees import (
    repo as repo,
)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process guards")


@pytest.mark.parametrize(
    "change", ["disappear", "renamed-link", "persistent", "permission"]
)
def test_1393_dependency_scan_restarts_without_losing_incoming_links(
    repo: Path,
    github: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    candidate = topic(repo)
    active = topic(repo, "active")
    git(repo, "worktree", "lock", str(active))
    tree = active / "out" / "pytest-old"
    renamed = tree.with_name("pytest-new")
    tree.mkdir(parents=True)
    if change == "renamed-link":
        (tree / "input").symlink_to(candidate / "source.txt")
    github([pull(candidate)])
    opens = 0
    real_scandir = os.scandir

    def scandir(path: Any = ".") -> Any:
        nonlocal opens
        if path in {tree, renamed}:
            opens += 1
            if change == "permission":
                raise PermissionError(13, "Permission denied", str(path))
            if change == "persistent":
                current = Path(path)
                current.rename(renamed if current == tree else tree)
            elif opens == 1:
                if change == "renamed-link":
                    tree.rename(renamed)
                else:
                    tree.rmdir()
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    run = cleanup(repo, "--github", "--apply")
    if change == "disappear":
        assert run.returncode == 0, run.stderr
        assert not candidate.exists()
    elif change == "renamed-link":
        assert run.returncode == 0, run.stderr
        assert result(repo, candidate)["reason"] == "symlink-dependency"
        assert (renamed / "input").read_text() == (candidate / "source.txt").read_text()
    else:
        assert run.returncode == 1
        error = str(manifest(repo)["error"])
        if change == "persistent":
            assert opens == 3
            assert "Dependency scan could not finish" in error
        else:
            assert opens == 1
            assert "Permission denied" in error
        assert candidate.is_dir()
