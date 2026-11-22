"""Exercise real Linux mount namespace boundaries for cleanup (#1393)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_cleanup_worktrees import proc as proc
from tests.test_cleanup_worktrees import repo as repo
from tests.test_cleanup_worktrees import topic

HOLDER = """
import ctypes, json, mmap, os, subprocess, sys
from pathlib import Path
candidate, alias, mode = json.loads(sys.stdin.readline())
candidate, alias = Path(candidate), Path(alias)
if mode.endswith('-fd'):
    fd = (candidate / 'source.txt').open()
if mode == 'masked-cwd':
    os.chdir(candidate)
if mode == 'masked-map':
    map_file = ctypes.CDLL(None).mmap
    map_file.restype = ctypes.c_void_p
    with (candidate / 'source.txt').open() as source:
        address = map_file(None, ctypes.c_size_t(1), mmap.PROT_READ, mmap.MAP_PRIVATE,
                           source.fileno(), ctypes.c_long(0))
        assert address != ctypes.c_void_p(-1).value
if mode == 'alias' or mode.startswith('covered-'):
    subprocess.run(['mount', '--bind', str(candidate), str(alias)], check=True)
    if mode in {'covered-script', 'covered-symlink'}:
        script = alias / 'task.py' if mode == 'covered-script' else alias.with_suffix('.py')
        os.execv(sys.executable, [sys.executable, str(script)])
    if mode == 'covered-idle':
        subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', str(alias)], check=True)
        subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', str(candidate.parent)], check=True)
elif mode.startswith('chroot'):
    subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', str(alias)], check=True)
    os.chroot(alias)
    os.chdir('/')
else:
    subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', str(candidate.parent)], check=True)
sys.stdout.write('ready\\n')
sys.stdout.flush()
sys.stdin.read()
"""

PROBE = """
import json, os, socket, subprocess, sys
from pathlib import Path
from threading import Thread
from scripts import worktree_activity_inspector as inspector

repo, candidate, alias = map(Path, sys.argv[1:4])
mode, holder_src = sys.argv[4:6]
with subprocess.Popen(
    ['unshare', '--mount', '--propagation', 'private', sys.executable, '-c',
     holder_src],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
) as holder:
    try:
        holder.stdin.write(json.dumps([str(candidate), str(alias), mode]) + '\\n')
        holder.stdin.flush()
        assert holder.stdout.readline() == 'ready\\n'
        inspector.process_ids = lambda fd: iter([str(holder.pid)])
        server, client = socket.socketpair()
        with server, client:
            thread = Thread(target=inspector.handle, args=(server, repo, os.getuid(), 'test'))
            thread.start()
            def identity(path):
                st = path.stat()
                return [st.st_dev, st.st_ino]
            query = {'repository': identity(repo), 'candidates': {candidate.name: identity(candidate)}}
            client.sendall(json.dumps(query).encode() + b'\\n')
            with client.makefile('rb') as stream:
                reply = json.loads(stream.readline())
            thread.join()
        if mode in {'alias', 'covered-script', 'covered-symlink'}:
            assert reply.get('error') == 'unverifiable' and reply.get('pid') == holder.pid, reply
        else:
            expected = [holder.pid] if mode.endswith(('-fd', '-map', '-cwd')) else []
            assert reply == {'sha256': 'test', 'busy': {candidate.name: expected}}, reply
    finally:
        holder.terminate()
"""


@pytest.mark.parametrize(
    "mode",
    [
        "masked",
        "masked-fd",
        "masked-map",
        "masked-cwd",
        "alias",
        "covered-script",
        "covered-symlink",
        "covered-idle",
        "chroot",
        "chroot-fd",
    ],
)
def test_1393_mount_namespaces_do_not_hide_surviving_references(
    repo: Path, mode: str
) -> None:
    if (
        sys.platform != "linux"
        or not shutil.which("unshare")
        or not shutil.which("mount")
    ):
        pytest.skip("requires Linux mount namespaces")
    outer = [
        "unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--propagation",
        "private",
    ]
    if subprocess.run(
        [*outer, "true"], capture_output=True, check=False, timeout=10
    ).returncode:
        pytest.skip("this runner does not permit unprivileged mount namespaces")
    path = topic(repo)
    alias = repo.parent / "foreign-alias"
    alias.mkdir()
    if mode in {"covered-script", "covered-symlink"}:
        (path / "task.py").write_text(
            "import subprocess, sys\n"
            f"for target in [{str(alias)!r}, {str(path.parent)!r}]:\n"
            "    subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', target], check=True)\n"
            "print('ready', flush=True)\n"
            "sys.stdin.read()\n"
        )
        if mode == "covered-symlink":
            alias.with_suffix(".py").symlink_to(alias / "task.py")
    run = subprocess.run(
        [
            *outer,
            sys.executable,
            "-c",
            PROBE,
            str(repo),
            str(path),
            str(alias),
            mode,
            HOLDER,
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert run.returncode == 0, run.stderr
