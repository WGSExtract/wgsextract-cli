"""Local cleanup must protect aliases and worker-private references (#1393)."""

import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.test_cleanup_worktrees import cleanup, handle_requests, result, topic
from tests.test_cleanup_worktrees import inspector as inspector
from tests.test_cleanup_worktrees import proc as proc
from tests.test_cleanup_worktrees import repo as repo

if sys.platform == "linux":
    from scripts import cleanup_worktrees, worktree_activity_inspector

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process guards")


@pytest.mark.parametrize("use_inspector", [False, True])
def test_1393_live_process_option_value_keeps_referenced_worktree(
    repo: Path,
    proc: Path,
    inspector: socket.socket,
    monkeypatch: pytest.MonkeyPatch,
    use_inspector: bool,
) -> None:
    path = topic(repo)
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.read()",
            "--source=./scratch/stale/source.txt",
        ],
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            assert process.stdout.readline() == "ready\n"
            if use_inspector:
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
                (proc / str(process.pid)).symlink_to(Path("/proc") / str(process.pid))
                run = cleanup(repo)
            assert run.returncode == 0, run.stderr
            assert result(repo, path)["reason"] == "active-process"
        finally:
            process.terminate()


WORKER = """
import ctypes, os, sys, threading

libc = ctypes.CDLL(None, use_errno=True)
def hold():
    if not hasattr(libc, 'close_range') or libc.close_range(10000, 20000, 2):
        print('unavailable', flush=True)
        return
    with open(sys.stdin.readline().strip(), 'rb'):
        print('ready', flush=True)
        stop.wait()

stop = threading.Event()
thread = threading.Thread(target=hold)
thread.start()
thread.join()
"""


def test_1393_local_scan_detects_live_worker_private_descriptor(
    repo: Path, proc: Path
) -> None:
    path = topic(repo)
    with subprocess.Popen(
        [sys.executable, "-c", WORKER],
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        try:
            assert holder.stdin is not None and holder.stdout is not None
            holder.stdin.write(f"{path / 'source.txt'}\n")
            holder.stdin.flush()
            status = holder.stdout.readline()
            if status == "unavailable\n":
                pytest.skip("close_range(CLOSE_RANGE_UNSHARE) unavailable")
            assert status == "ready\n"
            (proc / str(holder.pid)).symlink_to(f"/proc/{holder.pid}")
            run = cleanup(repo)
            assert run.returncode == 0, run.stderr
            assert result(repo, path)["reason"] == "active-process"
        finally:
            holder.terminate()


@pytest.mark.parametrize("source_kind", ["tree", "subdirectory", "ancestor"])
def test_1393_local_audit_detects_external_bind_mount(
    repo: Path, proc: Path, source_kind: str
) -> None:
    if not shutil.which("unshare") or not shutil.which("mount"):
        pytest.skip("requires Linux mount namespaces")
    path = topic(repo)
    directory = path / "dir"
    directory.mkdir()
    alias = repo.parent / "foreign alias"
    alias.mkdir()
    source = {"tree": path, "subdirectory": directory, "ancestor": path.parent}[
        source_kind
    ]
    entry = Path(__file__).resolve().parents[1] / "scripts/cleanup_worktrees.py"
    run = subprocess.run(
        [
            "unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--propagation",
            "private",
            sys.executable,
            "-c",
            """
import os, subprocess, sys
alias, source, entry, proc = sys.argv[1:]
mounted = subprocess.run(['mount', '--bind', source, alias], check=False)
assert mounted.returncode == 0
sys.path.insert(0, os.path.dirname(os.path.dirname(entry)))
from scripts import cleanup_worktrees
from pathlib import Path
cleanup_worktrees.PROC = Path(proc)
sys.argv = [entry]
raise SystemExit(cleanup_worktrees.main())
""",
            str(alias),
            str(source),
            str(entry),
            str(proc),
        ],
        cwd=repo,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    if run.returncode and any(
        message in run.stderr
        for message in (
            "unshare failed: Operation not permitted",
            "unshare: write failed /proc/self/uid_map: Operation not permitted",
        )
    ):
        pytest.skip("this runner does not permit unprivileged mount namespaces")
    assert run.returncode == 0, run.stderr
    assert result(repo, path)["reason"] == "contains-mount"
