import csv
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from wgsextract_cli.core import execution_context, process_registry
from wgsextract_cli.core.dependencies import get_jar_dir
from wgsextract_cli.core.process_registry import proc_registry
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary
from wgsextract_cli.core.utils import popen
from wgsextract_cli.core.variant_files import verify_paths_exist


class TestPathNormalizationAndProcessRegistry(unittest.TestCase):
    def tearDown(self):
        proc_registry.processes.clear()

    def test_verify_paths_exist_expands_tilde_paths(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as tmp_dir:
            target = Path(tmp_dir) / "sample.fa"
            target.write_text(">chr1\nACGT\n", encoding="utf-8")

            tilde_path = str(target).replace(str(Path.home()), "~", 1)

            verify_paths_exist({"--input": tilde_path})

    def test_reference_library_expands_tilde_root(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as tmp_dir:
            ref_dir = Path(tmp_dir)
            fasta_path = ref_dir / "hg38.fa"
            fasta_path.write_text(">chr1\nACGT\n", encoding="utf-8")

            tilde_root = str(ref_dir).replace(str(Path.home()), "~", 1)

            lib = ReferenceLibrary(tilde_root)

            self.assertEqual(lib.fasta, str(fasta_path))

    def test_get_jar_dir_expands_configured_tilde_path(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as tmp_dir:
            tilde_path = str(Path(tmp_dir)).replace(str(Path.home()), "~", 1)

            with patch(
                "wgsextract_cli.core.config.settings", {"jar_directory": tilde_path}
            ):
                self.assertEqual(get_jar_dir(), tmp_dir)

    def test_popen_unregisters_finished_processes_for_same_command(self):
        cmd = [sys.executable, "-c", "import time; time.sleep(0.1)"]
        cmd_key = " ".join(cmd)

        proc1 = popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc2 = popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self.assertEqual(len(proc_registry.processes[cmd_key]), 2)

        proc1.wait()
        proc2.wait()
        # Registry cleanup happens on a watcher thread; poll with a deadline.
        deadline = time.monotonic() + 10
        while cmd_key in proc_registry.processes and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertNotIn(cmd_key, proc_registry.processes)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-tree regression")
def test_1643_windows_cleanup_kills_break_ignoring_descendants_in_both_trees(tmp_path):
    start = tmp_path / "start"

    def launch(index):
        child_pid = tmp_path / f"child-{index}.pid"
        grandchild_pid = tmp_path / f"grandchild-{index}.pid"
        grandchild = (
            "import os, signal, time; from pathlib import Path; "
            "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
            f"Path({str(grandchild_pid)!r}).write_text(str(os.getpid())); "
            "time.sleep(60)"
        )
        child = (
            "import os, signal, subprocess, sys, time; from pathlib import Path; "
            "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
            f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
            f"Path({str(child_pid)!r}).write_text(str(os.getpid())); "
            "time.sleep(60)"
        )
        parent = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import subprocess, sys, time\n"
                "from pathlib import Path\n"
                f"start = Path({str(start)!r})\n"
                "deadline = time.monotonic() + 10\n"
                "while not start.exists() and time.monotonic() < deadline:\n"
                "    time.sleep(0.01)\n"
                "assert start.exists()\n"
                f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)",
            ],
            **process_registry.process_group_kwargs(),
        )
        return parent, (child_pid, grandchild_pid)

    registry = process_registry.ProcessRegistry()
    parents = []
    pid_files = []
    descendants = []
    try:
        for index in range(2):
            parent, files = launch(index)
            parents.append(parent)
            pid_files.extend(files)
            registry.register_process("launcher", parent)
        start.touch()
        deadline = time.monotonic() + 15
        while not all(path.exists() and path.stat().st_size > 0 for path in pid_files):
            assert all(parent.poll() is None for parent in parents)
            assert time.monotonic() < deadline, "descendants did not initialize"
            time.sleep(0.05)
        descendants = [int(path.read_text()) for path in pid_files]
        registry.cleanup()
        for parent in parents:
            parent.wait(timeout=5)
        for pid in descendants:
            listing = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            assert not any(
                len(row) > 1 and row[1] == str(pid)
                for row in csv.reader(listing.stdout.splitlines())
            ), f"descendant {pid} survived cleanup"
        descendants.clear()
    finally:
        remaining = descendants + [
            parent.pid for parent in parents if parent.poll() is None
        ]
        for pid in remaining:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                timeout=10,
            )
        for parent in parents:
            parent.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-tree regression")
def test_1699_windows_cleanup_drains_stderr_after_launcher_exits(tmp_path):
    start = tmp_path / "start"
    child_pid = tmp_path / "child.pid"
    script = """
import subprocess, sys, time
from pathlib import Path

deadline = time.monotonic() + 10
while not Path(sys.argv[1]).exists() and time.monotonic() < deadline:
    time.sleep(0.01)
assert Path(sys.argv[1]).exists()
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=sys.stderr,
)
Path(sys.argv[2]).write_text(str(child.pid))
"""
    scope = object()
    registry = process_registry.proc_registry
    with execution_context.bound(process_scope=scope):
        parent = popen(
            [sys.executable, "-c", script, str(start), str(child_pid)],
            stderr=subprocess.PIPE,
        )
    assert parent.stderr is not None
    drained = threading.Event()

    def drain():
        parent.stderr.read()
        drained.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    child = None
    try:
        # Start the descendant only after the registry has assigned the job.
        start.touch()
        assert parent.wait(timeout=10) == 0
        child = psutil.Process(int(child_pid.read_text()))
        assert not drained.wait(0.25), "child must still hold stderr open"

        registry.cleanup(scope)

        assert drained.wait(5), "the child kept stderr open after cleanup"
        child.wait(timeout=5)
    finally:
        registry.cleanup(scope)
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        with suppress(psutil.NoSuchProcess):
            if child is None and child_pid.exists():
                child = psutil.Process(int(child_pid.read_text()))
            if child is not None:
                child.kill()
        reader.join(timeout=5)
        if not reader.is_alive():
            parent.stderr.close()


if __name__ == "__main__":
    unittest.main()
