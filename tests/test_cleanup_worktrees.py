"""Regression tests for data loss during stale-worktree cleanup (#1393)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import TextIO, cast

import pytest

REPO = "WGSExtract/wgsextract-cli"
PACKAGE_URL = "https://example.invalid/linux-64/demo-1.0-py_0.conda"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process guards")
if sys.platform == "linux":
    from scripts import cleanup_worktrees, worktree_activity_inspector


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
    (root / "pixi.lock").write_text(f"packages:\n- conda: {PACKAGE_URL}\n")
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
    monkeypatch.setenv(
        "PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", os.defpath)
    )

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


def test_1393_offline_audit_fixture_works_with_path_unset(
    repo: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if not shutil.which("git", path=os.defpath):
        pytest.skip("Git is not installed on the OS default search path")
    path = topic(repo)
    monkeypatch.delenv("PATH", raising=False)
    request.getfixturevalue("github")(status=91)

    run = cleanup(repo)
    assert run.returncode == 0, run.stderr
    assert result(repo, path)["reason"] == "github-not-checked"
    assert path.is_dir()


def generated_env(path: Path) -> Path:
    env = path / ".pixi/envs/dev"
    launcher = "#!/bin/sh\n"
    write(env / "CACHEDIR.TAG", "Signature: 8a477f597d28d172789f06886806bc55\n")
    write(
        env / "conda-meta/pixi",
        json.dumps(
            {
                "environment_name": "dev",
                "manifest_path": str(path / "pixi.toml"),
                "pixi_version": "0.70.0",
                "environment_lock_file_hash": "1",
            }
        ),
    )
    write(
        env / "conda-meta/demo-1.0-py_0.json",
        json.dumps(
            {
                "name": "demo",
                "version": "1.0",
                "build": "py_0",
                "url": PACKAGE_URL,
                "files": ["bin/demo"],
                "paths_data": {
                    "paths": [
                        {
                            "_path": "bin/demo",
                            "sha256": hashlib.sha256(
                                b"#!/build-time-prefix/bin/sh\n"
                            ).hexdigest(),
                            "prefix_placeholder": "/build-time-prefix",
                            "sha256_in_prefix": hashlib.sha256(
                                launcher.encode()
                            ).hexdigest(),
                        }
                    ]
                },
            }
        ),
    )
    write(env / "bin/demo", launcher)
    return env


def test_1393_offline_then_archive_and_remove_only_exact_merged_checkout(
    repo: Path, github: Callable[..., None]
) -> None:
    path = topic(repo)
    head = git(path, "rev-parse", "HEAD")
    write(path / "out/run/handoff.md", "completed steps\n")
    write(path / "scratch/test.py", "print('unique scratch source')\n")
    generated_env(path)
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
    assert (
        archive / "scratch/test.py"
    ).read_text() == "print('unique scratch source')\n"
    assert archive.stat().st_mode & 0o077 == 0
    assert not path.exists()
    assert git(repo, "rev-parse", "refs/heads/stale") == head
    assert git(repo, "branch", "--show-current") == "main"


def test_1393_trims_verified_environment_without_removing_mixed_worktree(
    repo: Path, github: Callable[..., None]
) -> None:
    path = topic(repo)
    env = generated_env(path)
    sample = path / "out/model.bam"
    write(sample, "irreplaceable sample")
    write(path / ".pixi/notes.py", "local scratch work")
    write(path / "scratch/analysis.py", "print('evidence')\n")
    (repo / "sample-link").symlink_to(sample)
    github([pull(path)])

    run = cleanup(repo, "--github", "--apply")
    assert run.returncode == 0, run.stderr
    assert env.is_dir()
    run = cleanup(repo, "--github", "--trim-caches")
    assert run.returncode == 0, run.stderr
    assert result(repo, path)["reason"] == "cache-eligible"
    assert env.is_dir()

    run = cleanup(repo, "--github", "--trim-caches", "--apply")
    assert run.returncode == 0, run.stderr
    record = result(repo, path)
    assert record["reason"] == "cache-pruned"
    assert record["pruned_caches"] == [".pixi/envs"]
    assert not env.exists()
    assert git(path, "branch", "--show-current") == "stale"
    assert sample.read_text() == "irreplaceable sample"
    assert (path / ".pixi/notes.py").read_text() == "local scratch work"
    saved = repo / str(record["archive"]) / "scratch/analysis.py"
    assert saved.read_text() == (path / "scratch/analysis.py").read_text()


@pytest.mark.parametrize(
    "blocker",
    [
        "custom-file",
        "unlocked-package",
        "incoming-link",
        "incoming-parent",
        "different-head",
        "dirty",
    ],
)
def test_1393_never_prunes_a_cache_with_unverified_data_or_checkout(
    repo: Path, github: Callable[..., None], blocker: str
) -> None:
    path = topic(repo)
    env = generated_env(path)
    write(path / "out/model.bam", "keep")
    merged = pull(path)
    if blocker == "custom-file":
        write(env / "bin/private-data", "keep inside Pixi too")
    elif blocker == "unlocked-package":
        write(
            env / "conda-meta/local-1.0-py_0.json",
            json.dumps(
                {
                    "name": "local",
                    "version": "1.0",
                    "build": "py_0",
                    "url": "file:///scratch/local-1.0-py_0.conda",
                    "files": ["bin/private-data"],
                }
            ),
        )
        write(env / "bin/private-data", "keep the manually installed package")
    elif blocker == "incoming-link":
        (repo / "needs-env").symlink_to(env / "bin/demo")
    elif blocker == "incoming-parent":
        (repo / "needs-env").symlink_to(path / ".pixi", target_is_directory=True)
    elif blocker == "different-head":
        git(path, "commit", "--allow-empty", "-m", "Unpushed experiment")
    else:
        write(path / "new.py", "untracked script")
    github([merged])

    run = cleanup(repo, "--github", "--trim-caches", "--apply")
    assert run.returncode == 0, run.stderr
    assert result(repo, path)["pruned_caches"] == []
    assert (env / "bin/demo").is_file()
    if blocker in {"custom-file", "unlocked-package"}:
        assert (env / "bin/private-data").read_text().startswith("keep")


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
    (outgoing / "input").symlink_to(repo / "source.txt")
    git(outgoing, "add", "input")
    git(outgoing, "commit", "-m", "Keep external input")
    git(repo, "worktree", "lock", str(locked))
    github([pull(path) for path in (incoming, outgoing, locked, outside)])

    run = cleanup(repo, "--apply", "--github")
    assert run.returncode == 0, run.stderr
    assert result(repo, incoming)["reason"] == "symlink-dependency"
    assert result(repo, outgoing)["reason"] == "symlink-dependency"
    assert result(repo, locked)["reason"] == "git-locked"
    assert result(repo, outside)["reason"] == "outside-canonical-scratch"
    assert (outside / "input").read_text() == "committed changes for incoming\n"


@pytest.mark.parametrize("hold", ["cwd", "file", "relative-script", "external-alias"])
def test_1393_an_active_process_blocks_cleanup(
    repo: Path, proc: Path, github: Callable[..., None], hold: str
) -> None:
    path = topic(repo)
    env = generated_env(path)
    write(path / "out/model.bam", "sample to retain")
    code = "import sys; "
    if hold == "file":
        code += "f = open('scratch/stale/source.txt'); "
    code += "print('ready', flush=True); sys.stdin.read()"
    argv = [sys.executable, "-c", code]
    if hold in {"relative-script", "external-alias"}:
        (path / "task.py").write_text(code)
        git(path, "add", "task.py")
        git(path, "commit", "-m", "Track the worktree task")
        # Python has closed task.py by the time its top-level code runs.
        alias = repo.parent / "external-script.py"
        alias.symlink_to(path / "task.py")
        argv = [
            sys.executable,
            "../external-script.py"
            if hold == "external-alias"
            else "./scratch/../scratch/stale/task.py",
        ]
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
            run = cleanup(repo, "--apply", "--github", "--trim-caches")
            assert run.returncode == 0, run.stderr
            assert result(repo, path)["reason"] == "active-process"
            assert env.is_dir()
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
    pid = os.getpid()
    (proc / str(pid)).symlink_to(Path("/proc") / str(pid))
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


@pytest.fixture
def inspector(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[socket.socket]:
    # On Linux the privileged service runs as root; this socket uses our UID.
    path = Path("inspector.sock")
    monkeypatch.setattr(cleanup_worktrees, "INSPECTOR_SOCKET", path)
    monkeypatch.setattr(cleanup_worktrees, "INSPECTOR_UID", os.geteuid())
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen()
        listener.settimeout(5)
        yield listener


def handle_requests(
    listener: socket.socket, repo: Path, count: int, digest: str
) -> None:
    for _ in range(count):
        conn, _ = listener.accept()
        with conn:
            worktree_activity_inspector.handle(conn, repo, os.geteuid(), digest)


def wait_for_zombie(pid: int) -> None:
    status = Path(f"/proc/{pid}/status")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(
            line.startswith("State:\tZ") for line in status.read_text().splitlines()
        ):
            return
        time.sleep(0.01)
    pytest.fail(f"PID {pid} did not become a zombie")


@pytest.mark.parametrize("holder_uid", [0, 1001])
@pytest.mark.parametrize("use_inspector", [True, False], ids=["inspector", "local"])
def test_1393_activity_is_not_limited_to_the_socket_owner_uid(
    repo: Path,
    proc: Path,
    inspector: socket.socket,
    monkeypatch: pytest.MonkeyPatch,
    holder_uid: int,
    use_inspector: bool,
) -> None:
    path = topic(repo)
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.read()",
        ],
        cwd=path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            assert process.stdout.readline() == "ready\n"
            # Keep the OS process, cwd and descriptors real, and change only the
            # observed UID to avoid requiring elevated CI permissions.
            observed_uid = holder_uid if holder_uid != os.geteuid() else 1002
            live_proc = Path("/proc") / str(process.pid)
            if use_inspector:
                real_proc_text = worktree_activity_inspector.proc_text

                def credential_status(fd: int, name: str) -> TextIO:
                    stream = real_proc_text(fd, name)
                    if (
                        name != "status"
                        or Path(os.readlink(f"/proc/self/fd/{fd}")) != live_proc
                    ):
                        return stream
                    with stream:
                        return StringIO(
                            "".join(
                                f"Uid:\t{observed_uid}\t{observed_uid}\t{observed_uid}\t{observed_uid}\n"
                                if line.startswith("Uid:")
                                else line
                                for line in stream
                            )
                        )

                monkeypatch.setattr(
                    worktree_activity_inspector, "proc_text", credential_status
                )
                monkeypatch.setattr(
                    worktree_activity_inspector,
                    "process_ids",
                    lambda fd: iter([str(process.pid)]),
                )
                with ThreadPoolExecutor(max_workers=1) as pool:
                    handled = pool.submit(
                        handle_requests,
                        inspector,
                        repo,
                        1,
                        cleanup_worktrees.INSPECTOR_SHA256,
                    )
                    run = cleanup(repo, "--inspector")
                    handled.result()
            else:
                process_link = proc / str(process.pid)
                process_link.symlink_to(live_proc)
                real_stat = Path.stat

                def credential_stat(
                    self: Path, *, follow_symlinks: bool = True
                ) -> os.stat_result:
                    info = real_stat(self, follow_symlinks=follow_symlinks)
                    if self == process_link:
                        fields = list(info)
                        fields[4] = observed_uid
                        return os.stat_result(fields)
                    return info

                monkeypatch.setattr(Path, "stat", credential_stat)
                run = cleanup(repo)
            assert run.returncode == 0, run.stderr
            assert result(repo, path)["reason"] == "active-process"
            assert path.is_dir()
        finally:
            process.terminate()


@pytest.mark.parametrize(
    "worker_alive", [True, False], ids=["live-worker", "fully-exited"]
)
@pytest.mark.parametrize("use_inspector", [True, False], ids=["inspector", "local"])
def test_1393_zombie_leader_cannot_hide_a_live_worktree_thread(
    repo: Path,
    proc: Path,
    inspector: socket.socket,
    monkeypatch: pytest.MonkeyPatch,
    worker_alive: bool,
    use_inspector: bool,
) -> None:
    path = topic(repo)
    code = ""
    if worker_alive:
        code = """
import ctypes, os, sys, threading
def worker():
    os.chdir(sys.argv[1])
    print(threading.get_native_id(), flush=True)
    sys.stdin.read()
threading.Thread(target=worker).start()
ctypes.CDLL(None).pthread_exit(None)
"""
    with subprocess.Popen(
        [sys.executable, "-c", code, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            if worker_alive:
                worker = int(process.stdout.readline())
                assert worker != process.pid
                assert Path(f"/proc/{process.pid}/task/{worker}/cwd").readlink() == path
            wait_for_zombie(process.pid)
            monkeypatch.setattr(
                worktree_activity_inspector,
                "process_ids",
                lambda fd: iter([str(process.pid)]),
            )
            if use_inspector:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    handled = pool.submit(
                        handle_requests,
                        inspector,
                        repo,
                        1,
                        cleanup_worktrees.INSPECTOR_SHA256,
                    )
                    run = cleanup(repo, "--inspector")
                    handled.result()
            else:
                (proc / str(process.pid)).symlink_to(Path("/proc") / str(process.pid))
                run = cleanup(repo)
            if worker_alive:
                assert run.returncode == 1, run.stdout
                assert result(repo, path)["reason"] == "activity-unverified"
                assert f"PID {process.pid}" in str(manifest(repo)["error"])
            else:
                assert run.returncode == 0, run.stderr
                assert result(repo, path)["reason"] == "github-not-checked"
            assert path.is_dir()
        finally:
            process.terminate()


@pytest.mark.parametrize("alias", [False, True], ids=["relative", "external-symlink"])
def test_1393_inspector_blocks_real_relative_script_over_socket(
    repo: Path,
    inspector: socket.socket,
    github: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    alias: bool,
) -> None:
    path = topic(repo)
    (path / "task.py").write_text(
        "import sys\nprint('ready', flush=True)\nsys.stdin.read()\n"
    )
    git(path, "add", "task.py")
    git(path, "commit", "-m", "Track running task")
    github([pull(path)])
    argument = "./scratch/../scratch/stale/task.py"
    if alias:
        (repo.parent / "external-task").symlink_to(path / "task.py")
        argument = "../external-task"
    with subprocess.Popen(
        [sys.executable, argument],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            assert process.stdout.readline() == "ready\n"
            monkeypatch.setattr(
                worktree_activity_inspector,
                "process_ids",
                lambda fd: iter([str(process.pid)]),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                handled = pool.submit(
                    handle_requests,
                    inspector,
                    repo,
                    1,
                    cleanup_worktrees.INSPECTOR_SHA256,
                )
                run = cleanup(repo, "--apply", "--github", "--inspector")
                handled.result()
            assert run.returncode == 0, run.stderr
            assert result(repo, path)["reason"] == "active-process"
            assert path.is_dir()
        finally:
            process.terminate()


@pytest.mark.parametrize("matching_digest", [True, False])
def test_1393_inspector_needs_matching_version_before_removal(
    repo: Path,
    inspector: socket.socket,
    github: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    matching_digest: bool,
) -> None:
    path = topic(repo)
    github([pull(path)])
    # Isolate the host's processes, but exercise real registered-worktree checks and IPC.
    monkeypatch.setattr(worktree_activity_inspector, "process_ids", lambda fd: iter(()))
    digest = cleanup_worktrees.INSPECTOR_SHA256 if matching_digest else "wrong"
    with ThreadPoolExecutor(max_workers=1) as pool:
        handled = pool.submit(
            handle_requests, inspector, repo, 3 if matching_digest else 1, digest
        )
        run = cleanup(repo, "--apply", "--github", "--inspector")
        handled.result()
    if matching_digest:
        assert run.returncode == 0, run.stderr
        assert not path.exists()
    else:
        assert run.returncode == 1
        assert path.is_dir()
        assert "version mismatch" in str(manifest(repo)["error"])


@pytest.mark.parametrize(
    "invalid", ["traversal", "symlink", "unregistered", "wrong-repo", "wrong-inode"]
)
def test_1393_inspector_rejects_requests_for_unapproved_targets(
    repo: Path,
    inspector: socket.socket,
    invalid: str,
) -> None:
    path = topic(repo)
    name = path.name
    if invalid == "traversal":
        name = "../stale"
    if invalid == "symlink":
        name = "alias"
        (path.parent / name).symlink_to(path)
    if invalid == "unregistered":
        name = "unregistered"
        (path.parent / name).mkdir()
    root_stat, path_stat = repo.stat(), path.stat()
    query = {
        "repository": [root_stat.st_dev, root_stat.st_ino + (invalid == "wrong-repo")],
        "candidates": {
            name: [path_stat.st_dev, path_stat.st_ino + (invalid == "wrong-inode")]
        },
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        handled = pool.submit(handle_requests, inspector, repo, 1, "test")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(cleanup_worktrees.INSPECTOR_SOCKET))
            client.sendall(json.dumps(query).encode() + b"\n")
            with client.makefile("rb") as stream:
                assert json.loads(stream.readline()) == {"error": "unverifiable"}
        handled.result()
    assert path.is_dir()


def test_1393_missing_inspector_never_falls_back_to_removal(
    repo: Path,
    github: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = topic(repo)
    github([pull(path)])
    monkeypatch.setattr(cleanup_worktrees, "INSPECTOR_SOCKET", Path("missing.sock"))
    monkeypatch.setattr(cleanup_worktrees, "INSPECTOR_UID", os.geteuid())
    run = cleanup(repo, "--apply", "--github", "--inspector")
    assert run.returncode == 1
    assert result(repo, path)["reason"] == "activity-unverified"
    assert path.is_dir()


def test_1393_private_unrelated_mount_is_not_a_worktree_alias() -> None:
    path = Path("/home/owner/project/scratch/topic")
    mount = worktree_activity_inspector.Mount
    normal = mount("8:1", Path("/"), Path("/"))
    private_tmp = mount("8:1", Path("/isolated/tmp"), Path("/tmp"))
    worktree_activity_inspector.check_mounts({normal, private_tmp}, {path: (1, 1)})
    alias = mount("8:1", path, Path("/some-alias"))
    with pytest.raises(worktree_activity_inspector.Unverifiable):
        worktree_activity_inspector.check_mounts(
            {normal, private_tmp, alias},
            {path: (1, 1)},
        )
