"""Regression tests for data loss during stale-worktree cleanup (#1393)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import cast

import pytest

from scripts import cleanup_worktrees

REPO = "WGSExtract/wgsextract-cli"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process guards")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE
    ).strip()


@pytest.fixture
def proc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Limit the process view to this test's children, not the CI host's daemons.
    proc = tmp_path / "proc"
    proc.mkdir()
    monkeypatch.setattr(cleanup_worktrees, "PROC", proc)
    return proc


@pytest.fixture
def repo(tmp_path: Path, proc: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    git(tmp_path, "init", "-b", "main", str(root))
    git(root, "config", "user.name", "Worktree Test")
    git(root, "config", "user.email", "cleanup@example.invalid")
    git(root, "remote", "add", "origin", f"https://github.com/{REPO}.git")
    (root / ".gitignore").write_text("/scratch/\n/out/\n/.pixi/\n")
    (root / "source.txt").write_text("committed source\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial commit")
    monkeypatch.chdir(root)
    return root


def topic(root: Path, name: str = "stale", path: Path | None = None) -> Path:
    path = path or root / "scratch" / name
    git(root, "worktree", "add", "-b", name, str(path))
    (path / "source.txt").write_text(f"committed changes for {name}\n")
    git(path, "commit", "-am", f"Change on {name}")
    return path


def pull(path: Path, number: int = 42, **changes: object) -> dict[str, object]:
    return {
        "number": number,
        "state": "MERGED",
        "mergedAt": "2024-01-01T00:00:00Z",
        "author": {"login": "theontho"},
        "baseRefName": "main",
        "headRefName": git(path, "branch", "--show-current"),
        "headRefOid": git(path, "rev-parse", "HEAD"),
        "headRepository": {"nameWithOwner": REPO},
        **changes,
    }


@pytest.fixture
def github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    def install(*pages: list[dict[str, object]], status: int = 0) -> None:
        response = [
            {
                "data": {
                    "viewer": {"login": "theontho"},
                    "repository": {
                        "nameWithOwner": REPO,
                        "pullRequests": {
                            "nodes": page,
                            "pageInfo": {"hasNextPage": i + 1 < len(pages)},
                        },
                    },
                }
            }
            for i, page in enumerate(pages)
        ]
        gh.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{json.dumps(response)}'\nexit {status}\n"
        )
        gh.chmod(0o755)

    return install


def cleanup(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    stdout, stderr = StringIO(), StringIO()
    assert root == Path.cwd()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            status = cleanup_worktrees.main(list(args))
        except SystemExit as exc:
            status = cast(int, exc.code)
    return subprocess.CompletedProcess(
        args, status, stdout.getvalue(), stderr.getvalue()
    )


def manifest(root: Path) -> dict[str, object]:
    record = max(
        (root / "out" / "worktree-cleanup").glob("*.json"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    return cast(dict[str, object], json.loads(record.read_text()))


def result(root: Path, path: Path) -> dict[str, object]:
    entries = cast(list[dict[str, object]], manifest(root)["worktrees"])
    return next(entry for entry in entries if entry["path"] == str(path))


def write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)


def test_1393_offline_then_archive_and_remove_only_exact_merged_checkout(
    repo: Path, github: Callable[..., None]
) -> None:
    path = topic(repo)
    head = git(path, "rev-parse", "HEAD")
    write(path / "out/run/handoff.md", "completed steps\n")
    write(path / ".pixi/envs/dev/cache.bin", "regenerable\n")
    github(status=91)

    run = cleanup(repo)
    assert run.returncode == 0, run.stderr
    assert result(repo, path)["reason"] == "github-not-checked"
    assert cleanup(repo, "--apply").returncode == 2
    assert path.is_dir()

    github([pull(path)])
    run = cleanup(repo, "--apply", "--github")
    assert run.returncode == 0, run.stderr
    retired = result(repo, path)
    assert retired["reason"] == "removed"
    archive = repo / str(retired["archive"])
    assert (archive / "out/run/handoff.md").read_text() == "completed steps\n"
    assert archive.stat().st_mode & 0o077 == 0
    assert not path.exists()
    assert git(repo, "rev-parse", "refs/heads/stale") == head
    assert git(repo, "branch", "--show-current") == "main"


@pytest.mark.parametrize(
    ("relative", "data", "reason"),
    [
        ("not-ignored.py", "new script", "dirty-or-untracked"),
        ("scratch/test.py", "unique test script", "unknown-data"),
        (".pixi/notes.py", "unique notes outside envs", "unknown-data"),
        ("out/model.bam", "sample data", "unknown-data"),
        ("out/output.log", "x" * (2 * 1024 * 1024), "evidence-too-large"),
        ("out/credentials.json", "private information", "unknown-data"),
        ("out/invalid.log", "header\0binary", "non-text-evidence"),
        ("source.txt", "uncommitted tracked change", "dirty-or-untracked"),
    ],
    ids=[
        "untracked",
        "ignored-source",
        "pixi-notes",
        "genome",
        "large",
        "credential",
        "binary",
        "tracked",
    ],
)
def test_1393_preserves_unknown_or_dirty_data(
    repo: Path, github: Callable[..., None], relative: str, data: str, reason: str
) -> None:
    path = topic(repo)
    write(path / relative, data)
    github([pull(path)])
    run = cleanup(repo, "--apply", "--github")
    assert run.returncode == 0, run.stderr
    assert str(result(repo, path)["reason"]).split(":")[0] == reason
    assert (path / relative).read_text() == data


def test_1393_latest_pr_ownership_and_exact_tip_protect_unmerged_commits(
    repo: Path, github: Callable[..., None]
) -> None:
    reused = topic(repo, "reused")
    ahead = topic(repo, "ahead")
    other = topic(repo, "other")
    preserved = pull(ahead)
    git(ahead, "commit", "--allow-empty", "-m", "Unpushed experiment")
    github(
        [pull(reused, 1), preserved],
        [
            pull(other, 43, author={"login": "someone-else"}),
            pull(reused, 44, state="CLOSED", mergedAt=None),
        ],
    )

    run = cleanup(repo, "--apply", "--github")
    assert run.returncode == 0, run.stderr
    assert result(repo, reused)["reason"] == "latest-pull-request-not-merged-to-main"
    assert result(repo, ahead)["reason"] == "head-not-exact-merged-head"
    assert result(repo, other)["reason"] == "pull-request-not-owned"
    assert all(path.is_dir() for path in (reused, ahead, other))


def test_1393_blocks_links_locked_and_outside_worktrees(
    repo: Path, github: Callable[..., None]
) -> None:
    incoming = topic(repo, "incoming")
    outgoing = topic(repo, "outgoing")
    locked = topic(repo, "locked")
    outside = topic(repo, "outside", repo.parent / "outside")
    (outside / "input").symlink_to(incoming / "source.txt")
    (outgoing / ".pixi/envs").mkdir(parents=True)
    (outgoing / ".pixi/envs/input").symlink_to(repo / "source.txt")
    git(repo, "worktree", "lock", str(locked))
    github([pull(path) for path in (incoming, outgoing, locked, outside)])

    run = cleanup(repo, "--apply", "--github")
    assert run.returncode == 0, run.stderr
    assert result(repo, incoming)["reason"] == "symlink-dependency"
    assert result(repo, outgoing)["reason"] == "symlink-dependency"
    assert result(repo, locked)["reason"] == "git-locked"
    assert result(repo, outside)["reason"] == "outside-canonical-scratch"
    assert (outside / "input").read_text() == "committed changes for incoming\n"


@pytest.mark.parametrize("hold", ["cwd", "file", "relative-script"])
def test_1393_an_active_process_blocks_cleanup(
    repo: Path, proc: Path, github: Callable[..., None], hold: str
) -> None:
    path = topic(repo)
    code = "import sys; "
    if hold == "file":
        code += "f = open('scratch/stale/source.txt'); "
    code += "print('ready', flush=True); sys.stdin.read()"
    argv = [sys.executable, "-c", code]
    if hold == "relative-script":
        (path / "task.py").write_text(code)
        git(path, "add", "task.py")
        git(path, "commit", "-m", "Track the worktree task")
        # Python has closed task.py by the time its top-level code runs.
        argv = [sys.executable, "./scratch/../scratch/stale/task.py"]
    github([pull(path)])
    with subprocess.Popen(
        argv,
        cwd=path if hold == "cwd" else repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            assert process.stdout.readline() == "ready\n"
            (proc / str(process.pid)).symlink_to(Path("/proc") / str(process.pid))
            run = cleanup(repo, "--apply", "--github")
            assert run.returncode == 0, run.stderr
            assert result(repo, path)["reason"] == "active-process"
            assert path.is_dir()
        finally:
            process.terminate()


def test_1393_github_error_never_falls_back_to_removal(
    repo: Path, github: Callable[..., None]
) -> None:
    path = topic(repo)
    github([pull(path)], status=1)
    run = cleanup(repo, "--apply", "--github")
    assert run.returncode == 1
    assert path.is_dir()
    assert "gh api failed" in str(manifest(repo)["error"])


def test_1393_cannot_remove_without_process_visibility(
    repo: Path,
    proc: Path,
    github: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = topic(repo)
    (proc / "123").mkdir()
    github([pull(path)])

    def unreadable(process: Path) -> Iterator[Path]:
        raise PermissionError(f"cannot inspect {process.name}")

    monkeypatch.setattr(cleanup_worktrees, "process_paths", unreadable)
    run = cleanup(repo, "--apply", "--github")
    assert run.returncode == 1
    assert path.is_dir()
    assert "cannot inspect" in str(manifest(repo)["error"])


def test_1393_parallel_cleanup_cannot_remove_another_runs_candidate(
    repo: Path, github: Callable[..., None]
) -> None:
    import fcntl

    path = topic(repo)
    github([pull(path)])
    with (repo / ".git/cleanup-worktrees.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        run = cleanup(repo, "--apply", "--github")
    assert run.returncode == 1
    assert "holds the repository lock" in run.stderr
    assert path.is_dir()
