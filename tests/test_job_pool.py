"""core.job_pool: chunks of work side by side within a CPU and memory
budget, as `vcf impute` runs GLIMPSE2's chunks (#1118)."""

import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress

import pytest

from wgsextract_cli import api
from wgsextract_cli.core import execution_context
from wgsextract_cli.core.job_pool import Job, run_jobs
from wgsextract_cli.core.utils import popen, run_command


class _Tracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running: list[str] = []
        self.memory = 0
        self.peak_jobs = 0
        self.peak_memory = 0
        self.events: list[str] = []

    def job(self, name: str, memory: int = 0, group: str | None = None) -> Job:
        def run() -> None:
            with self.lock:
                self.running.append(name)
                self.memory += memory
                self.peak_jobs = max(self.peak_jobs, len(self.running))
                self.peak_memory = max(self.peak_memory, self.memory)
                self.events.append(f"start {name}")
            time.sleep(0.05)
            with self.lock:
                self.running.remove(name)
                self.memory -= memory
                self.events.append(f"end {name}")

        return Job(name, run, memory, group)


def test_1118_jobs_run_side_by_side_within_workers_and_memory():
    tracker = _Tracker()
    sizes = [5, 4, 3, 2, 1, 1, 1]
    jobs = [tracker.job(f"j{i}", size) for i, size in enumerate(sizes)]

    run_jobs(jobs, workers=3, memory=6)

    assert sorted(e for e in tracker.events if e.startswith("end")) == sorted(
        f"end j{i}" for i in range(len(sizes))
    )
    assert tracker.peak_jobs <= 3
    assert tracker.peak_memory <= 6


def test_1118_jobs_that_fit_run_at_the_same_time():
    # Each job waits for the other: run one at a time, the barrier breaks.
    both = threading.Barrier(2, timeout=10)
    jobs = [Job(name, lambda: both.wait(), 3) for name in ("a", "b")]

    run_jobs(jobs, workers=2, memory=6)


def test_1118_a_job_larger_than_the_budget_still_runs_alone():
    tracker = _Tracker()

    run_jobs([tracker.job("big", 10), tracker.job("small", 1)], workers=4, memory=5)

    assert tracker.events[:2] == ["start big", "end big"]
    assert tracker.peak_jobs == 1


def test_1118_a_group_follow_up_runs_once_its_jobs_have_finished():
    tracker = _Tracker()
    jobs = [tracker.job("a1", group="a"), tracker.job("a2", group="a")]
    jobs += [tracker.job(f"b{i}", group="b") for i in range(3)]

    run_jobs(
        jobs, workers=2, then={"a": tracker.job("join a"), "b": tracker.job("join b")}
    )

    events = tracker.events
    for group, members in (("a", ["a1", "a2"]), ("b", ["b0", "b1", "b2"])):
        joined = events.index(f"start join {group}")
        assert all(events.index(f"end {m}") < joined for m in members)


def test_1118_a_failed_job_stops_the_running_tools_and_queued_jobs():
    ran: list[str] = []

    def long_tool() -> None:
        try:
            run_command([sys.executable, "-c", "import time; time.sleep(60)"])
        finally:
            ran.append("tool ended")

    def fail() -> None:
        time.sleep(1)
        raise ValueError("chunk failed")

    jobs = [
        Job("tool", long_tool),
        Job("fails", fail),
        Job("queued", lambda: ran.append("queued")),
    ]
    start = time.monotonic()
    with pytest.raises(ValueError, match="chunk failed"):
        run_jobs(jobs, workers=2)

    # The sibling's tool was stopped, not left running; the queued job never ran.
    assert ran == ["tool ended"]
    assert time.monotonic() - start < 30
    # Tools still start afterwards.
    assert (
        run_command(
            [sys.executable, "-c", "print('ok')"], capture_output=True
        ).stdout.strip()
        == "ok"
    )


def test_1118_cancelling_starts_no_more_jobs():
    token = api.CancelToken()
    ran: list[str] = []
    jobs = [Job("cancels", token.cancel), Job("queued", lambda: ran.append("queued"))]

    with execution_context.bound(cancel=token), pytest.raises(api.Cancelled):
        run_jobs(jobs, workers=1)

    assert ran == []


def test_1415_cancelling_during_submission_leaves_other_worker_idle():
    token = api.CancelToken()
    considering_queued_job = threading.Event()
    cancelled = threading.Event()
    ran = threading.Event()

    class QueuedMemory(int):
        def __radd__(self, used: int) -> int:
            if not considering_queued_job.is_set():
                # Cancel while the scheduler is deciding whether this job fits.
                considering_queued_job.set()
                assert cancelled.wait(timeout=10)
            else:
                # If submitted anyway, let the job run before shutdown can
                # cancel its future and hide the late submission.
                assert ran.wait(timeout=10)
            return used + int(self)

    def cancel_in_worker() -> None:
        assert considering_queued_job.wait(timeout=10)
        token.cancel()
        cancelled.set()

    jobs = [
        Job("cancels", cancel_in_worker, memory=1),
        Job("queued", ran.set, memory=QueuedMemory(1)),
    ]
    with execution_context.bound(cancel=token), pytest.raises(api.Cancelled):
        run_jobs(jobs, workers=2, memory=2)

    assert not ran.is_set()


def test_1415_cancelling_while_waiting_stops_only_its_tool_tree():
    token = api.CancelToken()
    returncodes: list[int] = []
    leaf = (
        "import socket, sys\n"
        "with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=30) as conn:\n"
        "    conn.recv(1)\n"
    )
    launcher = (
        "import subprocess, sys; "
        "sys.exit(subprocess.call([sys.executable, '-c', *sys.argv[1:]]))"
    )

    with (
        socket.create_server(("127.0.0.1", 0)) as listener,
        execution_context.ContextThreadPoolExecutor(max_workers=2) as caller,
    ):
        listener.settimeout(10)
        port = str(listener.getsockname()[1])

        def run_tool() -> None:
            result = run_command(
                [sys.executable, "-c", launcher, leaf, port],
                capture_output=True,
                check=False,
            )
            returncodes.append(result.returncode)

        with execution_context.bound(process_scope=object()):
            unrelated = caller.submit(
                run_command, [sys.executable, "-c", leaf, port], capture_output=True
            )
        unrelated_connection, _ = listener.accept()
        with unrelated_connection:
            with execution_context.bound(cancel=token):
                operation = caller.submit(run_jobs, [Job("tool", run_tool)], workers=1)

            # The grandchild's connection must close without the test
            # releasing either tool.
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(10)
                token.cancel()
                with suppress(ConnectionResetError):
                    assert connection.recv(1) == b""
                with pytest.raises(api.Cancelled):
                    operation.result(timeout=10)
            unrelated_connection.sendall(b"x")
            assert unrelated.result(timeout=10).returncode == 0

    assert len(returncodes) == 1 and returncodes[0] != 0


def test_1415_cancelling_as_last_job_finishes_still_stops_its_tool():
    token = api.CancelToken()
    tools: list[subprocess.Popen[bytes]] = []

    class FinishingMemory(int):
        def __rsub__(self, used: int) -> int:
            # The last future is already done when its memory is released.
            token.cancel()
            return used - int(self)

    def start_tool() -> None:
        tools.append(
            popen(
                [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                stdin=subprocess.PIPE,
            )
        )

    try:
        with execution_context.bound(cancel=token), pytest.raises(api.Cancelled):
            run_jobs([Job("tool", start_tool, memory=FinishingMemory(1))], workers=1)

        (tool,) = tools
        assert tool.wait(timeout=10) != 0
    finally:
        for tool in tools:
            assert tool.stdin is not None
            tool.stdin.close()
            tool.wait(timeout=10)
