"""Server job history and event replay regressions."""

import sys

import pytest

from wgsextract_cli.server import jobs
from wgsextract_cli.server.jobs import JobManager
from wgsextract_cli.server.operations import RequestError, find


def test_1209_event_replay_keeps_recent_events_and_final_status(tmp_path):
    manager = JobManager(
        tmp_path / "jobs",
        config_path=None,
        worker_command=[
            sys.executable,
            "-c",
            "import json, sys\n"
            "sys.stdin.readline()\n"
            "for step in range(1100):\n"
            "    print(json.dumps({'event': 'progress', 'message': str(step)}))\n"
            "print(json.dumps({'event': 'result', 'result': {'status': 'ok'}}))",
        ],
    )
    try:
        job = manager.submit(
            find("example-genome/list"), {"target_root": str(tmp_path)}
        )
        done = manager.wait(job.id, 10)
        assert done is not None and done.status == "succeeded"
        events, finished = manager.events(job, 0, 0)
        assert finished and events[-1]["status"] == "succeeded"
        assert events[0]["seq"] > 1
        assert events[-2]["message"] == "1099"
        resumed, _ = manager.events(job, events[-2]["seq"], 0)
        assert resumed == [events[-1]]
    finally:
        manager.shutdown()


def test_1209_evicting_finished_jobs_keeps_their_disk_records(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_FINISHED_JOBS", 2)
    manager = JobManager(
        tmp_path / "jobs",
        config_path=None,
        worker_command=[str(tmp_path / "missing-worker")],
    )
    op = find("example-genome/list")
    params = {"target_root": str(tmp_path)}
    job_id = "86f82f25-86e5-423f-acec-1c996d97e259"
    try:
        first = manager.submit(op, params, job_id=job_id)
        for _ in range(jobs.MAX_FINISHED_JOBS):
            latest = manager.submit(op, params)
        old = manager.get(first.id)
        assert old is not None and old.status == "failed"
        assert manager.events(old, 0, 0) == ([], True)
        assert manager.events(latest, 0, 0)[0][-1]["status"] == "failed"
        assert first.id in {job["id"] for job in manager.summaries("failed")}
        assert manager.summaries("running") == []
        with pytest.raises(RequestError) as error:
            manager.submit(op, params, job_id=job_id)
        assert error.value.status == 409
    finally:
        manager.shutdown()
    restarted = JobManager(tmp_path / "jobs", config_path=None)
    try:
        history = restarted.summaries("failed")
        assert {first.id, latest.id} <= {job["id"] for job in history}
    finally:
        restarted.shutdown()
