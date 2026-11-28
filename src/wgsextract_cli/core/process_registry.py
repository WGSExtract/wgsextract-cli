import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import Sequence
from typing import Any

from wgsextract_cli.core._windows_job import WindowsJob
from wgsextract_cli.core.download_progress import CancelEvent
from wgsextract_cli.core.errors import WGSExtractError

_owned_groups: weakref.WeakKeyDictionary[subprocess.Popen[Any], int] = (
    weakref.WeakKeyDictionary()
)
_owned_jobs: weakref.WeakKeyDictionary[subprocess.Popen[Any], WindowsJob] = (
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
            if sys.platform == "win32":
                # Popen closes the primary thread handle before returning. Assign
                # immediately, but the launcher may have already spawned a child.
                handle = getattr(process, "_handle", None)
                if isinstance(handle, int) and process not in _owned_jobs:
                    try:
                        _owned_jobs[process] = WindowsJob(handle)
                    except OSError:
                        if process.returncode is None and process.poll() is None:
                            logging.warning(
                                "Could not assign process %s to a Windows job; "
                                "falling back to taskkill.",
                                process.pid,
                                exc_info=True,
                            )
            refusing = self._refusing_all or (
                scope is not None and scope in self._refusing
            )
        if refusing:
            # Started after cancel(): it raced the cleanup, so stop it now.
            terminate_trees([process])

    def unregister_process(self, key: str, process: subprocess.Popen[Any]) -> None:
        if sys.platform == "win32" and (job := _owned_jobs.get(process)):
            if job.has_processes():
                from wgsextract_cli.core.execution_context import context_thread

                # Returning from the launcher is not a request to stop its tools.
                def unregister_after_job() -> None:
                    while job.has_processes():
                        time.sleep(0.1)
                    self._unregister_process(key, process)

                context_thread(unregister_after_job, daemon=True).start()
                return
        self._unregister_process(key, process)

    def _unregister_process(self, key: str, process: subprocess.Popen[Any]) -> None:
        with self._lock:
            processes = self.processes.get(key)
            if not processes:
                return
            processes.discard(process)
            self._scopes.pop(process, None)
            if not processes:
                del self.processes[key]
        if sys.platform == "win32" and (job := _owned_jobs.get(process)):
            job.close()

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


def is_sigpipe(returncode: int) -> bool:
    sigpipe = getattr(signal, "SIGPIPE", None)
    return sigpipe is not None and returncode in (-sigpipe, 128 + sigpipe)


def wait_for_pipeline(
    procs: Sequence[subprocess.Popen[bytes]],
    cancel_event: CancelEvent | None,
    description: str,
    *,
    fail_fast: bool,
) -> list[int | None]:
    # Infinite Windows waits defer Python's main-thread signal handlers.
    if cancel_event is None and not fail_fast and sys.platform != "win32":
        return [proc.wait() for proc in procs]
    for proc in procs:
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                raise WGSExtractError(f"{description} cancelled")
            if fail_fast:
                statuses = [stage.poll() for stage in procs]
                if any(
                    code not in (None, 0)
                    and (index == len(procs) - 1 or not is_sigpipe(code))
                    for index, code in enumerate(statuses)
                ):
                    return statuses
            try:
                proc.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
    return [proc.wait() for proc in procs]


def _terminate_windows_trees(processes: list[subprocess.Popen[Any]]) -> None:
    unowned = []
    owned = []
    for proc in processes:
        if job := _owned_jobs.get(proc):
            job.terminate()
            owned.append(proc)
        else:
            unowned.append(proc)

    _terminate_unowned_windows_trees(unowned)
    deadline = time.monotonic() + 2
    for proc in owned:
        try:
            # Wait for the launcher itself, not any pipe relays in _RelayedPopen.
            subprocess.Popen.wait(proc, timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            logging.warning("Timed out waiting for cancelled process %s.", proc.pid)


def _terminate_unowned_windows_trees(
    processes: list[subprocess.Popen[Any]],
) -> None:
    command = ["taskkill", "/F", "/T"]
    pids = [str(proc.pid) for proc in processes if proc.poll() is None]
    if not pids:
        return
    for pid in pids:
        command.extend(("/PID", pid))
    label = ", ".join(pids)
    try:
        # Only for unowned processes. Batch PIDs for a single bounded launch.
        result = subprocess.run(command, capture_output=True, timeout=10, check=False)
        if result.returncode:
            logging.warning(
                "Process tree %s could not be terminated (taskkill exit %s).",
                label,
                result.returncode,
            )
    except (OSError, subprocess.TimeoutExpired):
        logging.warning("Process %s could not be terminated.", label, exc_info=True)
    for proc in processes:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                logging.debug(
                    "Process %s could not be killed.", proc.pid, exc_info=True
                )


def terminate_trees(processes: list[subprocess.Popen[Any]]) -> None:
    """Terminate these processes' trees, escalating Unix groups to SIGKILL."""
    if not processes:
        return
    groups: set[int] = set()
    if sys.platform == "win32":
        _terminate_windows_trees(processes)
        return
    else:
        for proc in processes:
            owned_group = _owned_groups.get(proc)
            if proc.poll() is not None and owned_group is None:
                continue
            try:
                pgid = owned_group
                if pgid is None:
                    pgid = os.getpgid(proc.pid)
                if pgid == proc.pid:
                    groups.add(pgid)
                    os.killpg(pgid, signal.SIGTERM)
                else:
                    proc.terminate()
            except OSError:
                logging.debug(
                    "Process %s could not be terminated.", proc.pid, exc_info=True
                )

        # The launcher can exit on SIGTERM while a descendant ignores it.
        time.sleep(0.5)
        for pgid in groups:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                logging.debug(
                    "Process group %s could not be killed.", pgid, exc_info=True
                )
    for proc in processes:
        if proc.pid not in groups and proc.poll() is None:
            try:
                if _owned_groups.get(proc) is None or proc.returncode is None:
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
