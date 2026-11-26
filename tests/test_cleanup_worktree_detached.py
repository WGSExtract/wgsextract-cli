"""Real Git regressions for safely retiring detached worktrees (#1393)."""

import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.test_cleanup_worktrees import cleanup, git, pull, result, write
from tests.test_cleanup_worktrees import github as github
from tests.test_cleanup_worktrees import proc as proc
from tests.test_cleanup_worktrees import repo as repo

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process guards")


def test_1393_detached_ancestor_needs_opt_in_and_round_trips_evidence(
    repo: Path, github: Callable[..., None]
) -> None:
    path = repo / "scratch" / "certify-old"
    git(repo, "worktree", "add", "--detach", str(path))
    original = git(path, "rev-parse", "HEAD")
    git(repo, "commit", "--allow-empty", "-m", "Advance main")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    write(path / "out/result.log", "certification evidence\n")
    github([])

    run = cleanup(repo, "--github", "--apply")
    assert run.returncode == 0, run.stderr
    assert path.is_dir()

    run = cleanup(repo, "--github", "--include-detached")
    assert run.returncode == 0, run.stderr
    planned = result(repo, path)
    assert planned["reason"] == "eligible"
    assert not git(repo, "for-each-ref", str(planned["recovery"]))
    assert path.is_dir()

    run = cleanup(repo, "--github", "--include-detached", "--apply")
    assert run.returncode == 0, run.stderr
    record = result(repo, path)
    assert record["reason"] == "removed"
    assert not path.exists()
    assert git(repo, "rev-parse", str(record["recovery"])) == original
    assert (repo / str(record["archive"]) / "out/result.log").read_text() == (
        "certification evidence\n"
    )


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_1393_detached_finished_pr_head_remains_locally_recoverable(
    repo: Path, github: Callable[..., None], state: str
) -> None:
    path = repo / "scratch" / "rt-experiment"
    git(repo, "worktree", "add", "--detach", str(path))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (path / "source.txt").write_text("work not in main\n")
    git(path, "commit", "-am", "Detached work")
    head = git(path, "rev-parse", "HEAD")
    github(
        [
            pull(
                path,
                state=state,
                closedAt="2024-01-01T00:00:00Z",
                headRefName="experiment",
            )
        ]
    )

    run = cleanup(repo, "--github", "--include-detached", "--apply")
    assert run.returncode == 0, run.stderr
    record = result(repo, path)
    assert record["reason"] == "removed"
    assert not path.exists()
    assert git(repo, "rev-parse", str(record["recovery"])) == head
    assert git(repo, "show", f"{record['recovery']}:source.txt") == "work not in main"


def test_1393_detached_removal_does_not_overwrite_an_existing_recovery_ref(
    repo: Path, github: Callable[..., None]
) -> None:
    path = repo / "scratch" / "rt-ref-conflict"
    git(repo, "worktree", "add", "--detach", str(path))
    git(path, "commit", "--allow-empty", "-m", "Unmerged PR tip")
    recovery = f"refs/worktree-cleanup/detached/{git(path, 'rev-parse', 'HEAD')}"
    original = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", recovery, original)
    github([pull(path, headRefName="experiment")])

    run = cleanup(repo, "--github", "--include-detached", "--apply")
    assert run.returncode == 1
    assert path.is_dir()
    assert git(repo, "rev-parse", recovery) == original


@pytest.mark.parametrize("old_value_only", [False, True])
def test_1393_detached_main_checkout_keeps_work_recoverable_only_from_reflog(
    repo: Path, github: Callable[..., None], old_value_only: bool
) -> None:
    path = repo / "scratch" / "rt-reflog"
    git(repo, "worktree", "add", "--detach", str(path))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (path / "source.txt").write_text("experiment no longer at HEAD\n")
    git(path, "commit", "-am", "Unpublished detached experiment")
    abandoned = git(path, "rev-parse", "HEAD")
    git(path, "reset", "--hard", "origin/main")
    # Clear ORIG_HEAD so only the worktree's HEAD reflog preserves the commit.
    git(path, "update-ref", "ORIG_HEAD", "HEAD")
    if old_value_only:
        git(path, "reflog", "delete", "HEAD@{1}")
        assert abandoned not in git(path, "reflog", "show", "--format=%H")
    github([])

    run = cleanup(repo, "--github", "--include-detached", "--apply")
    assert run.returncode == 0, run.stderr
    assert result(repo, path)["reason"] == "detached-reflog-unpreserved"
    assert (
        git(path, "show", f"{abandoned}:source.txt") == "experiment no longer at HEAD"
    )


def test_1393_detached_unique_commit_and_open_or_reused_pr_are_retained(
    repo: Path, github: Callable[..., None]
) -> None:
    paths = {}
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    for name in ("unique", "open", "reused", "other-author"):
        path = repo / "scratch" / name
        git(repo, "worktree", "add", "--detach", str(path))
        git(path, "commit", "--allow-empty", "-m", f"Unmerged {name} commit")
        paths[name] = path
    github(
        [
            pull(paths["open"], 1, state="OPEN", headRefName="open"),
            pull(paths["reused"], 2, headRefName="reused"),
            pull(paths["open"], 3, state="OPEN", headRefName="reused"),
            pull(
                paths["other-author"], 4, headRefName="other", author={"login": "other"}
            ),
        ]
    )

    run = cleanup(repo, "--github", "--include-detached", "--apply")
    assert run.returncode == 0, run.stderr
    assert all(path.is_dir() for path in paths.values())
    assert result(repo, paths["open"])["reason"] == "pull-request-not-finished-on-main"
    assert result(repo, paths["other-author"])["reason"] == "pull-request-not-owned"
    for name in ("unique", "reused"):
        assert result(repo, paths[name])["reason"] == "detached-head-not-on-main-or-pr"


def test_1393_detached_still_requires_safe_location_and_contents(
    repo: Path, github: Callable[..., None]
) -> None:
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    paths = {
        "outside-canonical-scratch": repo.parent / "wt-cert42",
        "symlink-dependency": repo / "scratch" / "rt-linked",
        "dirty-or-untracked": repo / "scratch" / "rt-dirty",
        "git-operation-in-progress": repo / "scratch" / "rt-bisect",
    }
    for path in paths.values():
        git(repo, "worktree", "add", "--detach", str(path))
    (paths["outside-canonical-scratch"] / "input").symlink_to(
        paths["symlink-dependency"] / "source.txt"
    )
    write(paths["dirty-or-untracked"] / "untracked.txt", "keep this\n")
    git(paths["git-operation-in-progress"], "bisect", "start", "--no-checkout")
    github([])

    run = cleanup(repo, "--github", "--include-detached", "--apply")
    assert run.returncode == 0, run.stderr
    for reason, path in paths.items():
        assert result(repo, path)["reason"] == reason
        assert path.is_dir()


def test_1393_named_build_outputs_do_not_pin_a_finished_checkout(
    repo: Path, github: Callable[..., None]
) -> None:
    path = repo / "scratch" / "rt-build"
    git(repo, "worktree", "add", "--detach", str(path))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    write(path / "scratch/build/helper.o", "generated build artifact")
    write(path / "scratch/build/with\nnewline.o", "another generated artifact")
    write(path / "out/cert.log", "evidence\n")
    (path / "out/trace.log").write_bytes(b"\0binary trace")
    write(path / "state/last-run.json", "{}\n")
    (repo / ".git/info/exclude").write_text("/state/\n")
    github([])
    args = ("--github", "--include-detached", "--apply")

    run = cleanup(repo, *args)
    assert run.returncode == 0, run.stderr
    assert path.is_dir()

    run = cleanup(
        repo,
        *args,
        "--disposable-output",
        "scratch/build/*.o",
        "--disposable-output",
        "out/*.log",
    )
    assert run.returncode == 0, run.stderr
    record = result(repo, path)
    assert record["reason"] == "removed"
    assert record["disposable_outputs"] == [
        "out/trace.log",
        "scratch/build/helper.o",
        "scratch/build/with\nnewline.o",
    ]
    assert not path.exists()
    archive = repo / str(record["archive"])
    assert (archive / "out/cert.log").read_text() == "evidence\n"
    assert (archive / "state/last-run.json").read_text() == "{}\n"


@pytest.mark.parametrize(
    "blocker", ["directory", "link", "tracked", "untracked", "key"]
)
def test_1393_named_output_glob_cannot_override_safety_checks(
    repo: Path, github: Callable[..., None], blocker: str
) -> None:
    path = repo / "scratch" / "rt-build"
    git(repo, "worktree", "add", "--detach", str(path))
    artifact = path / "out/helper.o"
    if blocker == "directory":
        write(artifact / "sample.bin", "keep")
    elif blocker == "link":
        artifact.parent.mkdir()
        artifact.symlink_to(path / "source.txt")
    elif blocker == "key":
        artifact = artifact.with_name("private-key.o")
        write(artifact, "keep")
    else:
        write(artifact, "keep")
        if blocker == "tracked":
            git(path, "add", "-f", "out/helper.o")
        else:
            (path / ".gitignore").write_text("/scratch/\n")
            git(path, "commit", "-am", "Do not ignore out")
    git(repo, "update-ref", "refs/remotes/origin/main", git(path, "rev-parse", "HEAD"))
    github([])

    run = cleanup(
        repo,
        "--github",
        "--include-detached",
        "--disposable-output",
        "out/*.o",
        "--apply",
    )
    assert run.returncode == 0, run.stderr
    assert artifact.exists()
    assert result(repo, path)["disposable_outputs"] == []


def test_1393_replacement_at_planned_output_path_is_retained(
    repo: Path, github: Callable[..., None]
) -> None:
    path = repo / "scratch" / "rt-build"
    git(repo, "worktree", "add", "--detach", str(path))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    artifact = path / "out/helper.o"
    replacement = repo / "out/replacement.o"
    write(artifact, "generated build artifact")
    write(replacement, "new output not present at planning time")
    github([])
    executable = shutil.which("gh")
    assert executable is not None
    gh = Path(executable)
    shebang, _, response = gh.read_text().partition("\n")
    replace = shlex.join(["mv", "--", str(replacement), str(artifact)])
    gh.write_text(f"{shebang}\n{replace} || exit 1\n{response}")

    run = cleanup(
        repo,
        "--github",
        "--include-detached",
        "--disposable-output",
        "out/*.o",
        "--apply",
    )
    assert run.returncode == 0, run.stderr
    assert result(repo, path)["reason"] == "disposable-outputs-changed"
    assert artifact.read_text() == "new output not present at planning time"


def test_1393_replacement_during_final_batched_ignore_check_is_retained(
    repo: Path, github: Callable[..., None]
) -> None:
    path = repo / "scratch" / "rt-build"
    git(repo, "worktree", "add", "--detach", str(path))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    artifact = path / "out/helper.o"
    replacement = repo / "out/replacement.o"
    write(artifact, "original artifact")
    write(path / "out/other.o", "independent artifact")
    write(replacement, "replacement during final ignore check")
    github([])
    real_git = shutil.which("git")
    gh = shutil.which("gh")
    assert real_git is not None and gh is not None
    wrapper = Path(gh).with_name("git")
    write(
        wrapper,
        f"#!{sys.executable}\n"
        "import os, pathlib, subprocess, sys\n"
        f"real_git = {real_git!r}\n"
        "args = sys.argv[1:]\n"
        "if 'check-ignore' not in args:\n"
        "    os.execv(real_git, [real_git, *args])\n"
        "names = sys.stdin.buffer.read() if '--stdin' in args else b''\n"
        "if set(names.split(b'\\0')) != {b'out/helper.o', b'out/other.o', b''}:\n"
        "    sys.exit('expected both artifacts in the same ignore request')\n"
        "answer = subprocess.run([real_git, *args], input=names, capture_output=True)\n"
        f"counter = pathlib.Path({str(wrapper.with_name('passes'))!r})\n"
        "count = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(count))\n"
        "if count == 3:\n"
        f"    os.replace({str(replacement)!r}, {str(artifact)!r})\n"
        "sys.stdout.buffer.write(answer.stdout)\n"
        "sys.stderr.buffer.write(answer.stderr)\n"
        "sys.exit(answer.returncode)\n",
    )
    wrapper.chmod(0o755)

    run = cleanup(
        repo,
        "--github",
        "--include-detached",
        "--disposable-output",
        "out/*.o",
        "--apply",
    )
    assert run.returncode == 0, run.stderr
    assert result(repo, path)["reason"] == "changed-during-archive"
    assert artifact.read_text() == "replacement during final ignore check"


def test_1393_detached_checkout_in_use_is_retained(
    repo: Path, proc: Path, github: Callable[..., None]
) -> None:
    path = repo / "scratch" / "rt-busy"
    git(repo, "worktree", "add", "--detach", str(path))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    github([])
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        cwd=path,
        stdin=subprocess.PIPE,
    ) as process:
        (proc / str(process.pid)).symlink_to(Path("/proc") / str(process.pid))
        try:
            run = cleanup(repo, "--github", "--include-detached", "--apply")
            assert run.returncode == 0, run.stderr
            assert result(repo, path)["reason"] == "active-process"
            assert path.is_dir()
        finally:
            process.terminate()
