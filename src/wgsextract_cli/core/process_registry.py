import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import weakref
from typing import Any

from wgsextract_cli.core.download_progress import CancelEvent

_owned_groups: weakref.WeakKeyDictionary[subprocess.Popen[Any], int] = (
    weakref.WeakKeyDictionary()
)


def remember_process_group(process: subprocess.Popen[Any]) -> None:
    """Record the POSIX group of a tool launched with process_group_kwargs."""
    if sys.platform != "win32":
        _owned_groups[process] = process.pid


class ProcessRegistry:
    """Tracks running subprocesses so they can be terminated on exit.

    Each process belongs to the ``process_scope`` of the execution context
    that started it, so cancelling one library call stops only its tools."""

    def __init__(self) -> None:
        self.processes: dict[str, set[subprocess.Popen[Any]]] = {}
        self._scopes: weakref.WeakKeyDictionary[subprocess.Popen[Any], object] = (
            weakref.WeakKeyDictionary()
        )
        # Reentrant: signal handlers run cleanup() on the main thread, possibly
        # while that thread is already inside register/unregister.
        self._lock = threading.RLock()
        self._refusing_all = False
        self._refusing: set[object] = set()

    def register_process(self, key: str, process: subprocess.Popen[Any]) -> None:
        from wgsextract_cli.core.execution_context import current

        scope = current().process_scope
        with self._lock:
            self.processes.setdefault(key, set()).add(process)
            self._scopes[process] = scope
            refusing = self._refusing_all or (
                scope is not None and scope in self._refusing
            )
        if refusing:
            # Started after cancel(): it raced the cleanup, so stop it now.
            terminate_trees([process])

    def unregister_process(self, key: str, process: subprocess.Popen[Any]) -> None:
        with self._lock:
            processes = self.processes.get(key)
            if not processes:
                return
            processes.discard(process)
            self._scopes.pop(process, None)
            if not processes:
                del self.processes[key]

    def cancel(self, scope: object | None = None) -> None:
        """Terminate the registered processes of ``scope`` (every process when
        None), and any it registers until ``accept_new(scope)``."""
        with self._lock:
            if scope is None:
                self._refusing_all = True
            else:
                self._refusing.add(scope)
        self.cleanup(scope)

    def accept_new(self, scope: object | None = None) -> None:
        """Let processes of ``scope`` (None: all) run again after ``cancel()``."""
        with self._lock:
            if scope is None:
                self._refusing_all = False
            else:
                self._refusing.discard(scope)

    def cleanup(self, scope: object | None = None) -> None:
        """Terminate registered process trees (only ``scope``'s when given),
        escalating Unix groups to SIGKILL."""
        with self._lock:
            stopped: list[subprocess.Popen[Any]] = []
            for key in list(self.processes):
                processes = self.processes[key]
                chosen = {
                    p
                    for p in processes
                    if scope is None or self._scopes.get(p) is scope
                }
                stopped.extend(chosen)
                processes -= chosen
                if not processes:
                    del self.processes[key]
            for process in stopped:
                self._scopes.pop(process, None)
        terminate_trees(stopped)


def wait_with_cancel(
    process: subprocess.Popen[Any], cancel_event: CancelEvent | None = None
) -> bool:
    """Wait for a process, terminating its tree and reaping it on cancellation."""
    while True:
        if cancel_event and cancel_event.is_set():
            logging.info("Terminating process %s due to cancellation.", process.pid)
            terminate_trees([process])
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logging.warning(
                    "Timed out waiting for cancelled process %s.", process.pid
                )
            return False
        if process.poll() is not None:
            return process.returncode == 0
        time.sleep(0.1)


def terminate_trees(processes: list[subprocess.Popen[Any]]) -> None:
    """Terminate these processes' trees, escalating Unix groups to SIGKILL."""
    if not processes:
        return

    groups: set[int] = set()
    for proc in processes:
        owned_group = _owned_groups.get(proc)
        if proc.poll() is None or owned_group is not None:
            try:
                if sys.platform == "win32":
                    # CTRL_BREAK can exit the launcher while Java descendants
                    # keep running, losing the root needed by taskkill /T.
                    result = subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                    if result.returncode:
                        logging.warning(
                            "Process tree %s could not be terminated "
                            "(taskkill exit %s).",
                            proc.pid,
                            result.returncode,
                        )
                else:
                    pgid = owned_group
                    if pgid is None:
                        pgid = os.getpgid(proc.pid)
                    if pgid == proc.pid:
                        groups.add(pgid)
                        os.killpg(pgid, signal.SIGTERM)
                    else:
                        proc.terminate()
            except (OSError, subprocess.TimeoutExpired):
                log = logging.warning if sys.platform == "win32" else logging.debug
                log("Process %s could not be terminated.", proc.pid, exc_info=True)

    time.sleep(0.5)

    # The launcher can exit on SIGTERM while a descendant ignores it.
    for pgid in groups:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            logging.debug("Process group %s could not be killed.", pgid, exc_info=True)
    for proc in processes:
        if proc.pid not in groups and proc.returncode is None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                logging.debug(
                    "Process %s could not be killed.", proc.pid, exc_info=True
                )


proc_registry = ProcessRegistry()


def cleanup_processes() -> None:
    """Entry point for atexit and signal handlers."""
    proc_registry.cleanup()


atexit.register(cleanup_processes)


def process_group_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}
