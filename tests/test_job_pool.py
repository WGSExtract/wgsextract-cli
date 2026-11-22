"""core.job_pool: chunks of work side by side within a CPU and memory
budget, as `vcf impute` runs GLIMPSE2's chunks (#1118)."""

import sys
import threading
import time

import pytest

from wgsextract_cli.core.job_pool import Job, run_jobs
from wgsextract_cli.core.utils import run_command


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
