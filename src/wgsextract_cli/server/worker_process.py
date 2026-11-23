"""Starting and stopping job worker processes."""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
from typing import Any


def detached() -> dict[str, Any]:
    """Run workers in their own process group: a terminal's Ctrl-C stops the
    server, which then cancels them cleanly."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(pid: int) -> None:
    import psutil

    with contextlib.suppress(psutil.Error):
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            with contextlib.suppress(psutil.Error):
                child.kill()
        parent.kill()


def kill_worker(pid: int) -> None:
    """Kill a worker and everything it started."""
    kill_tree(pid)
    if sys.platform != "win32":
        # It leads its own process group (see detached): this also stops
        # what has left its tree.
        with contextlib.suppress(OSError):
            os.killpg(pid, signal.SIGKILL)


def discard_worker(worker: subprocess.Popen[str], timeout: float) -> None:
    """Kill a worker still waiting for its request, close its pipes and reap
    it."""
    kill_worker(worker.pid)
    for pipe in (worker.stdin, worker.stdout):
        if pipe is not None:
            with contextlib.suppress(OSError, ValueError):
                pipe.close()
    try:
        worker.wait(timeout)
    except subprocess.TimeoutExpired:
        logging.warning(f"Worker {worker.pid} did not stop after being killed.")
