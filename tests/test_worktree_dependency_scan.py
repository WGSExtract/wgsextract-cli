"""Exercise cleanup while another checkout's generated directories change (#1393)."""

import os
import shlex
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
    write,
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


@pytest.mark.parametrize("linked_parent", [False, True])
def test_1393_preserves_worktree_target_of_common_git_metadata_link(
    repo: Path, github: Callable[..., None], linked_parent: bool
) -> None:
    path = topic(repo)
    target = path / "shared-excludes"
    write(target, "*.local\n")
    git(path, "add", "shared-excludes")
    git(path, "commit", "-m", "Track shared Git excludes")
    exclude = repo / ".git" / "info" / "exclude"
    if linked_parent:
        outside = repo.parent / "git-info"
        exclude.parent.rename(outside)
        exclude.parent.symlink_to(outside, target_is_directory=True)
    exclude.unlink()
    exclude.symlink_to(target)
    github([pull(path)])

    run = cleanup(repo, "--apply", "--github")

    assert run.returncode == 0, run.stderr
    assert result(repo, path)["reason"] == "symlink-dependency"
    assert exclude.read_text() == "*.local\n"


def test_1393_preserves_hook_path_used_by_retained_linked_worktree(
    repo: Path, github: Callable[..., None]
) -> None:
    path = topic(repo)
    active = topic(repo, "active")
    git(repo, "worktree", "lock", str(active))
    hook = path / "hooks" / "pre-commit"
    write(hook, "#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    git(path, "add", "hooks")
    git(path, "commit", "-m", "Track shared hook")
    git(repo, "config", "extensions.worktreeConfig", "true")
    git(active, "config", "--worktree", "core.hooksPath", "../stale/hooks")
    git(active, "hook", "run", "pre-commit")
    github([pull(path)])

    run = cleanup(repo, "--apply", "--github")

    assert run.returncode == 0, run.stderr
    assert result(repo, path)["reason"] == "symlink-dependency"
    git(active, "hook", "run", "pre-commit")


@pytest.mark.parametrize(
    "location",
    [
        "default",
        "linked-default",
        "custom-absolute",
        "custom-relative",
        "receive",
        "config",
        "config-substitution",
        "config-glob",
        "config-nested",
    ],
)
def test_1393_preserves_primary_git_hooks_using_a_stale_worktree(
    repo: Path, github: Callable[..., None], location: str
) -> None:
    configured = location.startswith("config")
    if configured:
        version = git(repo, "version").split()[2].split(".")
        if tuple(map(int, version[:2])) < (2, 56):
            pytest.skip("config-defined hooks require Git 2.56")
    path = topic(repo)
    hook = "pre-receive" if location == "receive" else "pre-commit"
    target = path / "hooks" / ("lint script" if configured else hook)
    write(target, "#!/bin/sh\nexit 0\n")
    target.chmod(0o755)
    git(path, "add", "hooks")
    git(path, "commit", "-m", "Keep hook available to primary checkout")
    hooks = repo / ".git" / "hooks"
    cwd = repo
    if location == "linked-default":
        external = repo.parent / "operational-hooks"
        hooks.rename(external)
        hooks.symlink_to(external, target_is_directory=True)
    if location == "custom-absolute":
        hooks = repo.parent / "custom-hooks"
        hooks.mkdir()
        git(repo, "config", "core.hooksPath", str(hooks))
    elif location in {"custom-relative", "receive"}:
        if location == "receive":
            cwd = repo / ".git"
        hooks = target.parent
        git(repo, "config", "core.hooksPath", os.path.relpath(hooks, cwd))
    if configured:
        relative = target.relative_to(repo)
        if location == "config-substitution":
            command = (
                f'sh "$(git rev-parse --show-toplevel)"/{shlex.quote(str(relative))}'
            )
        elif location == "config-glob":
            command = f"sh scratch/st*/hooks/{shlex.quote(target.name)}"
        elif location == "config-nested":
            command = shlex.join(["sh", "-c", f"exec {shlex.quote(str(relative))}"])
        else:
            command = shlex.join(["sh", str(relative)])
        git(repo, "config", "hook.lint.command", command)
        git(repo, "config", "hook.lint.event", hook)
    elif hooks != target.parent:
        (hooks / hook).symlink_to(target)
    git(cwd, "hook", "run", hook)
    github([pull(path)])

    run = cleanup(repo, "--apply", "--github")
    if configured:
        assert path.is_dir()
        assert run.returncode == 1
        assert "config-hook-unverifiable: hook.lint.command" in str(
            manifest(repo)["error"]
        )
    else:
        assert run.returncode == 0, run.stderr
        assert result(repo, path)["reason"] == "symlink-dependency"
    git(cwd, "hook", "run", hook)


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
