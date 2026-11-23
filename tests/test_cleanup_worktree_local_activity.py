"""Local cleanup must protect aliases and worker-private references (#1393)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_cleanup_worktrees import cleanup, result, topic
from tests.test_cleanup_worktrees import proc as proc
from tests.test_cleanup_worktrees import repo as repo

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process guards")

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
    if run.returncode and "unshare failed: Operation not permitted" in run.stderr:
        pytest.skip("this runner does not permit unprivileged mount namespaces")
    assert run.returncode == 0, run.stderr
    assert result(repo, path)["reason"] == "contains-mount"
