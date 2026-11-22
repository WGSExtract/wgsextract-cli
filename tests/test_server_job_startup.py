"""Server job startup regressions (#1226): a job whose worker starts but
whose bookkeeping then fails must not leave the worker or its slot behind."""

import os
import subprocess
import sys

import psutil
import pytest

from wgsextract_cli.core.workspace_activity import desktop_data_dir
from wgsextract_cli.server.jobs import JobManager
from wgsextract_cli.server.operations import find

# A fake worker: it reads its request and marks its outdir as run; a job
# whose outdir ends in "hold" waits for a cancel line. While the file it is given exists,
# it closes its control pipe unread instead and lingers.
_STARTUP_WORKER = (
    "import json, os, sys, time\n"
    "if os.path.exists(sys.argv[1]):\n"
    "    os.close(0)\n"
    "    time.sleep(60)\n"
    "request = json.loads(sys.stdin.readline())\n"
    "open(os.path.join(request['params']['outdir'], 'ran'), 'w').close()\n"
    "if request['params']['outdir'].endswith('hold'):\n"
    "    sys.stdin.readline()\n"
    "print(json.dumps({'event': 'result', 'result': {'status': 'ok'}}))\n"
)


@pytest.fixture
def worker_pids(monkeypatch):
    pids: list[int] = []
    spawn = subprocess.Popen

    def record(*args, **kwargs):
        worker = spawn(*args, **kwargs)
        pids.append(worker.pid)
        return worker

    monkeypatch.setattr(subprocess, "Popen", record)
    return pids


def _outdir(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    return {"outdir": str(folder)}


def test_1226_failing_to_record_a_started_job_frees_its_slot(tmp_path, worker_pids):
    jobs = tmp_path / "jobs"
    worker = [sys.executable, "-c", _STARTUP_WORKER, str(tmp_path / "closed")]
    manager = JobManager(jobs, config_path=None, worker_command=worker)
    op = find("qc/fake-data")
    job_id = "86f82f25-86e5-423f-acec-1c996d97e259"
    try:
        holder = manager.submit(op, _outdir(tmp_path, "hold"))
        queued = manager.submit(op, _outdir(tmp_path, "queued"), job_id=job_id)
        assert (holder.status, queued.status) == ("running", "queued")
        # Its record can't be written once its worker has started.
        (jobs / f"{job_id}.json.tmp").mkdir()
        manager.cancel(holder.id)
        failed = manager.wait(job_id, 10)
        assert failed is not None and failed.status == "failed"
        assert failed.error is not None
        assert "Could not start the job" in failed.error["message"]
        # Its worker never got the job, so nothing ran in its folder.
        assert not (tmp_path / "queued" / "ran").exists()
        assert (job_id, "failed") in {
            (job["id"], job["status"]) for job in manager.summaries()
        }

        (jobs / f"{job_id}.json.tmp").rmdir()
        later = manager.submit(op, _outdir(tmp_path, "later"))
        done = manager.wait(later.id, 10)
        assert done is not None and done.status == "succeeded"
        assert len(worker_pids) == 3
        assert not [pid for pid in worker_pids if psutil.pid_exists(pid)]
    finally:
        manager.shutdown()
    # Once the disk recovered, the failure was saved too.
    restarted = JobManager(jobs, config_path=None)
    try:
        saved = restarted.get(job_id)
        assert saved is not None and saved.status == "failed"
        assert saved.error is not None
        assert "Could not start the job" in saved.error["message"]
    finally:
        restarted.shutdown()


def test_1226_a_worker_that_closes_its_control_pipe_is_stopped(tmp_path, worker_pids):
    closed = tmp_path / "closed"
    closed.touch()
    manager = JobManager(
        tmp_path / "jobs",
        config_path=None,
        # A request larger than a pipe's buffer: writing it fails once the
        # worker closes the pipe, however soon that is.
        allowed=[str(tmp_path / ("x" * 200) / str(i)) for i in range(1000)],
        worker_command=[sys.executable, "-c", _STARTUP_WORKER, str(closed)],
    )
    op = find("qc/fake-data")
    try:
        job = manager.submit(op, _outdir(tmp_path, "first"))
        failed = manager.wait(job.id, 10)
        assert failed is not None and failed.status == "failed"
        assert failed.error is not None
        assert "Could not start the job" in failed.error["message"]
        assert not psutil.pid_exists(worker_pids[0])

        closed.unlink()
        later = manager.submit(op, _outdir(tmp_path, "later"))
        done = manager.wait(later.id, 10)
        assert done is not None and done.status == "succeeded"
        assert [(job["id"], job["status"]) for job in manager.summaries()] == [
            (failed.id, "failed"),
            (later.id, "succeeded"),
        ]
    finally:
        manager.shutdown()


def test_1226_a_job_that_cannot_be_recorded_is_refused_without_a_trace(tmp_path):
    jobs = tmp_path / "jobs"
    worker = [sys.executable, "-c", _STARTUP_WORKER, str(tmp_path / "closed")]
    manager = JobManager(jobs, config_path=None, worker_command=worker)
    op = find("qc/fake-data")
    job_id = "86f82f25-86e5-423f-acec-1c996d97e259"
    try:
        (jobs / f"{job_id}.json.tmp").mkdir()
        with pytest.raises(OSError):
            manager.submit(op, _outdir(tmp_path, "first"), job_id=job_id)
        assert manager.summaries() == []

        (jobs / f"{job_id}.json.tmp").rmdir()
        retried = manager.submit(op, _outdir(tmp_path, "again"), job_id=job_id)
        done = manager.wait(retried.id, 10)
        assert done is not None and done.status == "succeeded"
    finally:
        manager.shutdown()


def test_1322_admitted_worker_survives_a_refused_move(tmp_path):
    worker = """
import json, pathlib, runpy, sys
marker = pathlib.Path(sys.argv[1])
if marker.exists():
    runpy.run_module("wgsextract_cli.server.worker", run_name="__main__")
else:
    sys.stdin.readline()
    marker.touch()
    sys.stdin.readline()
    print(json.dumps({"event": "result", "result": {"status": "ok"}}))
"""
    started = tmp_path / "started"
    manager = JobManager(
        tmp_path / "jobs",
        config_path=None,
        worker_command=[sys.executable, "-c", worker, str(started)],
    )
    op = find("example-genome/download")
    params = {"target_root": str(tmp_path), "dry_run": True}
    move = desktop_data_dir() / "workspace-activity" / f"move-{os.getpid()}-a"
    try:
        holder = manager.submit(op, params)
        queued = manager.submit(op, params)
        assert queued.status == "queued"
        # A mover can briefly publish a marker, but the queued job already
        # owns admission so that move cannot proceed.
        move.touch()
        manager.cancel(holder.id)

        done = manager.wait(queued.id, 20)
        assert done is not None and done.status == "succeeded", manager.log_path(
            queued.id
        ).read_text()
    finally:
        move.unlink(missing_ok=True)
        manager.shutdown()
