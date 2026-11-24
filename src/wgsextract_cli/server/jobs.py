"""Jobs: each request runs in its own worker process, so long operations run in
the background, can be cancelled, and survive in the job list across server
restarts.

A job's record is ``<state>/server/jobs/<id>.json``, its worker's log
``<id>.log``. The worker (``python -m wgsextract_cli.server.worker``) reads
the request from stdin, writes progress and the result as JSON lines to
stdout, and cancels the operation when it reads ``cancel`` or stdin closes.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from wgsextract_cli.api._params import with_config_defaults
from wgsextract_cli.core.file_lock import exclusive_lock
from wgsextract_cli.core.reference_assets import reference_genomes_dir
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.core.workspace_activity import WorkspaceLease, hold_workspace

from .job_paths import (
    INPUT_NAMES,
    beside_folder,
    beside_path,
    first_input,
    folder_key,
    is_local_folder,
)
from .operations import (
    Operation,
    RequestError,
    expand_paths,
    is_network_path,
    params_from_json,
)
from .transport import Job as JobView
from .transport import JobError, JobEvent, JobProgress, JobSummary, Kind, Status
from .worker_process import detached, discard_worker, kill_tree

FINISHED = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
RECORD_SCHEMA = "wgsextract.job/1"
MAX_FINISHED_JOBS = 128
MAX_JOB_EVENTS = 1024

# How long a cancelled worker may take to stop its tools and write its
# manifest before it is killed.
CANCEL_GRACE_SECONDS = 30.0
# A cancelled job's exit code, however it stopped: as for Ctrl-C on the CLI.
CANCELLED_EXIT_CODE = 130
# Result fields left out of compact job views: the manifest has them.
_VERBOSE_RESULT_FIELDS = ("provenance", "schema")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    op: str
    kind: Kind
    params: dict[str, Any]
    status: Status = "queued"
    created: str = field(default_factory=_now)
    started: str | None = None
    finished: str | None = None
    exit_code: int | None = None
    error: JobError | None = None
    progress: JobProgress | None = None
    result: dict[str, Any] | None = None
    cancel_requested: str | None = None
    card: bool | None = (
        None  # its result card, as the request asked; None: the server's default
    )
    server_pid: int = field(default_factory=os.getpid)
    events: deque[JobEvent] = field(
        default_factory=lambda: deque(maxlen=MAX_JOB_EVENTS), repr=False
    )

    @property
    def done(self) -> bool:
        return self.status in FINISHED

    def record(self) -> dict[str, Any]:
        """What is saved: everything but the event history."""
        data = {k: v for k, v in vars(self).items() if k != "events"}
        return {"schema": RECORD_SCHEMA, **data}

    def summary(self) -> JobSummary:
        data: JobSummary = {
            "id": self.id,
            "op": self.op,
            "status": self.status,
            "created": self.created,
        }
        if self.finished is not None:
            data["finished"] = self.finished
        return data

    def view(self, *, full: bool = False) -> JobView:
        """The job as a client sees it: unset fields left out and, unless
        ``full``, the result without its provenance."""
        data: JobView = {
            "id": self.id,
            "op": self.op,
            "kind": self.kind,
            "status": self.status,
            "created": self.created,
        }
        if full:
            data["params"] = self.params
        if self.started is not None:
            data["started"] = self.started
        if self.finished is not None:
            data["finished"] = self.finished
        if self.exit_code is not None:
            data["exit_code"] = self.exit_code
        if self.error is not None:
            data["error"] = self.error
        if self.progress is not None:
            data["progress"] = self.progress
        if self.cancel_requested is not None:
            data["cancel_requested"] = self.cancel_requested
        if self.result is not None:
            data["result"] = self.result
        if self.result is not None and not full:
            data["result"] = {
                k: v
                for k, v in self.result.items()
                if k not in _VERBOSE_RESULT_FIELDS and v not in (None, [], {})
            }
        return data


class JobManager:
    """Starts, tracks and cancels jobs. Thread-safe.

    Queries start at once; at most ``max_jobs`` other jobs run at a time and
    the rest wait in order. Jobs writing to the same folder run one at a time,
    since commands save fixed file names there. One manager owns a job folder
    at a time (an exclusive lock), so no two servers share their jobs.
    """

    def __init__(
        self,
        directory: Path,
        *,
        config_path: Path | None,
        max_jobs: int = 1,
        debug: bool = False,
        default_outdir: str | None = None,
        allowed: Iterable[str | os.PathLike[str]] | None = None,
        worker_command: list[str] | None = None,
        card: bool | None = None,
    ) -> None:
        self.directory = directory
        self.allowed = None if allowed is None else [os.fspath(a) for a in allowed]
        self.default_outdir = default_outdir
        self.config_path = config_path
        self.max_jobs = max(1, max_jobs)
        self.debug = debug
        self.card = card  # each job's result card; None: as config.toml says
        self.worker_command = worker_command or [
            sys.executable,
            "-m",
            "wgsextract_cli.server.worker",
        ]
        self._lock = threading.Condition()
        self._jobs: dict[str, Job] = {}
        self._finished: deque[str] = deque()
        self._queue: list[str] = []
        self._workers: dict[str, subprocess.Popen[str]] = {}
        # Folders each queued or running job writes in, for _pump.
        self._writes: dict[str, frozenset[str]] = {}
        self._workspace_leases: dict[str, WorkspaceLease] = {}
        self._cancelling: set[str] = set()
        # Finished jobs whose record or summary is not saved yet (a full
        # disk), in order: kept in memory and retried, never evicted.
        self._unsaved: dict[str, None] = {}
        self._stopping = False
        directory.mkdir(parents=True, exist_ok=True)
        # Job records and logs hold results and paths: this user's only.
        if sys.platform != "win32":
            directory.chmod(0o700)
        self._owner = contextlib.ExitStack()
        try:
            self._owner.enter_context(exclusive_lock(directory / "owner", timeout=0))
        except WGSExtractError:
            raise WGSExtractError(
                f"Another wgsextract server is using {directory}; stop it first."
            ) from None
        self._history = directory / "summaries.sqlite"
        self._load()

    # Records ---------------------------------------------------------------

    def _load(self) -> None:
        # The index is derived; rebuilding also picks up older records.
        self._history.unlink(missing_ok=True)
        with contextlib.closing(sqlite3.connect(self._history)) as db, db:
            db.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, summary TEXT)"
            )
            db.execute("CREATE INDEX jobs_status ON jobs (status)")
            self._recover_records(db)

    def _recover_records(self, db: sqlite3.Connection) -> None:
        for path in self.directory.glob("*.json"):
            job = self._read_record(path)
            if job is None:
                continue
            # This manager holds the folder's lock, so no other server is
            # running these jobs any more.
            if not job.done:
                job.status = "interrupted"
                job.finished = job.finished or _now()
                job.error = {
                    "kind": "cancelled",
                    "message": "The server stopped before the job finished; "
                    "rerun it to resume from its completed steps.",
                }
                self._save(job)
            self._index_summary(db, job)

    @staticmethod
    def _index_summary(db: sqlite3.Connection, job: Job) -> None:
        db.execute(
            "INSERT OR REPLACE INTO jobs (id, status, summary) VALUES (?, ?, ?)",
            (job.id, job.status, json.dumps(job.summary())),
        )

    def _read_record(self, path: Path) -> Job | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("schema", None)
            return Job(**data)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, AttributeError) as e:
            logging.warning(f"Ignoring unreadable job record {path}: {e}")
            return None

    def _save(self, job: Job) -> None:
        path = self.directory / f"{job.id}.json"
        staged = path.with_suffix(".json.tmp")
        staged.write_text(
            json.dumps(job.record(), indent=1), encoding="utf-8", newline="\n"
        )
        os.replace(staged, path)

    def log_path(self, job_id: str) -> Path:
        return self.directory / f"{job_id}.log"

    # Queries ---------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._get(job_id)

    def _get(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if job is None and re.fullmatch(r"[A-Za-z0-9-]+", job_id):
            return self._read_record(self.directory / f"{job_id}.json")
        return job

    def summaries(self, status: str | None = None) -> list[JobSummary]:
        with self._lock:
            jobs: dict[str, JobSummary] = {}
            if status is None or status in FINISHED:
                with contextlib.closing(sqlite3.connect(self._history)) as db:
                    rows = (
                        db.execute("SELECT summary FROM jobs")
                        if status is None
                        else db.execute(
                            "SELECT summary FROM jobs WHERE status = ?", (status,)
                        )
                    )
                    for (summary,) in rows:
                        item = json.loads(summary)
                        jobs[item["id"]] = item
            # Unfinished jobs, and finished ones not yet saved, are listed
            # from memory.
            for job in self._jobs.values():
                if (not job.done or job.id in self._unsaved) and (
                    status is None or job.status == status
                ):
                    jobs[job.id] = job.summary()
        return sorted(jobs.values(), key=lambda job: job["created"])

    def wait(self, job_id: str, timeout: float) -> Job | None:
        """The job once finished, or as it is after ``timeout`` seconds."""
        deadline = time.monotonic() + timeout
        with self._lock:
            job = self._get(job_id)
            while job is not None and not job.done:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._lock.wait(remaining)
            return job

    def events(
        self, job: Job, after: int, timeout: float
    ) -> tuple[list[JobEvent], bool]:
        """Events with ``seq`` above ``after``, waiting up to ``timeout`` for
        one; and whether the job has finished."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while True:
                new = [e for e in job.events if e["seq"] > after]
                remaining = deadline - time.monotonic()
                if new or job.done or remaining <= 0:
                    return new, job.done
                self._lock.wait(remaining)

    # Changes ---------------------------------------------------------------

    def check_job_id(self, job_id: str) -> None:
        """Reject invalid IDs and existing jobs before validating a submission."""
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            job_id,
        ):
            raise RequestError(
                400, "invalid_job_id", "Job ID must be a lowercase UUIDv4."
            )
        with self._lock:
            self._check_new_job_id(job_id)

    def _check_new_job_id(self, job_id: str) -> None:
        if job_id in self._jobs or (self.directory / f"{job_id}.json").exists():
            raise RequestError(409, "job_id_in_use", "Job ID is already in use.")

    def submit(
        self,
        op: Operation,
        params: Mapping[str, Any],
        *,
        card: bool | None = None,
        job_id: str | None = None,
    ) -> Job:
        try:
            workspace = hold_workspace()
        except WGSExtractError as error:
            raise RequestError(409, "workspace_busy", str(error)) from error
        with workspace.cleanup:
            return self._submit(
                op, params, card=card, job_id=job_id, workspace=workspace
            )

    def _submit(
        self,
        op: Operation,
        params: Mapping[str, Any],
        *,
        card: bool | None,
        job_id: str | None,
        workspace: WorkspaceLease,
    ) -> Job:
        if job_id is not None:
            self.check_job_id(job_id)
        effective = op.request_values(
            with_config_defaults(params_from_json(op, params))
        )
        if self.default_outdir and "outdir" in effective and not params.get("outdir"):
            effective["outdir"] = self.default_outdir
        job = Job(
            id=job_id
            or time.strftime("%Y%m%d-%H%M%S-", time.gmtime()) + secrets.token_hex(3),
            op=op.name,
            kind=op.kind,
            # Locks and workers see the folders the path checks approved.
            params=expand_paths(op, effective),
            card=card,
        )
        names = {f.name for f in op.fields()}
        # With no input to save beside, outputs would go to the working folder.
        if op.params.SAVES_IN_WORKING_FOLDER:
            in_working_folder = not (job.params.get("outdir") or self.default_outdir)
        else:
            in_working_folder = (
                not names & set(INPUT_NAMES)
                and not op.params.WRITES_BESIDE
                and not self._output_folder(op, job)
            )
        if op.kind != "query" and "outdir" in names and in_working_folder:
            raise RequestError(
                400,
                "usage",
                "'outdir' is required: with no configured output folder, this "
                "job would save into the server's working folder.",
            )
        with self._lock:
            self._check_new_job_id(job.id)
            if self._stopping:
                raise RequestError(503, "stopping", "The server is stopping.")
            self._jobs[job.id] = job
            self._writes[job.id] = self._write_folders(op, job)
            try:
                self._save(job)
            except OSError:
                # Nothing started: refuse the request and forget the job.
                del self._jobs[job.id]
                del self._writes[job.id]
                raise
            self._event(job, "status", status="queued")
            self._workspace_leases[job.id] = WorkspaceLease(
                workspace.name, workspace.cleanup.pop_all()
            )
            if op.kind == "query":
                self._start(job)
            else:
                self._queue.append(job.id)
                self._pump()
        return job

    def cancel(self, job_id: str) -> Job | None:
        """Cancel a job: a queued one at once, a running one in the background
        (its tools are stopped and it finishes as cancelled)."""
        with self._lock:
            job = self._get(job_id)
            if job is None or job.done:
                return job
            if job.id in self._queue:
                self._queue.remove(job.id)
                self._finish(job, "cancelled", error="Cancelled before it started.")
                return job
            worker = self._workers.get(job.id)
            if worker is not None and job.cancel_requested is None:
                self._cancelling.add(job.id)
                job.cancel_requested = _now()
                self._event(job, "status", status="cancelling")
                threading.Thread(
                    target=self._stop_worker, args=(job.id, worker), daemon=True
                ).start()
            return job

    def shutdown(self) -> None:
        """Stop: queued jobs never start and running ones are cancelled; both
        are recorded as interrupted."""
        with self._lock:
            self._stopping = True
            for job_id in self._queue:
                self._finish(
                    self._jobs[job_id], "interrupted", error="The server stopped."
                )
            self._queue.clear()
            workers = dict(self._workers)
            self._cancelling.update(workers)
        threads = [
            threading.Thread(target=self._stop_worker, args=item)
            for item in workers.items()
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with self._lock:
            # Let the followers record each job's final status.
            self._lock.wait_for(lambda: not self._workers, timeout=10)
        self._owner.close()

    # Internals (called with the lock held unless noted) --------------------

    def _event(
        self,
        job: Job,
        kind: Literal["status", "progress"],
        *,
        status: Status | Literal["cancelling"] | None = None,
        stage: str | None = None,
        message: str | None = None,
        fraction: float | None = None,
    ) -> None:
        seq = job.events[-1]["seq"] + 1 if job.events else 1
        event: JobEvent = {"seq": seq, "type": kind, "time": _now()}
        if status is not None:
            event["status"] = status
        if stage is not None:
            event["stage"] = stage
        if message is not None:
            event["message"] = message
        if fraction is not None:
            event["fraction"] = fraction
        job.events.append(event)
        self._lock.notify_all()

    def _pump(self) -> None:
        active = [self._jobs[i] for i in self._workers if self._jobs[i].kind != "query"]
        busy = set().union(*(self._writes.get(job.id, ()) for job in active))
        for job_id in list(self._queue):
            if len(active) >= self.max_jobs or self._stopping:
                break
            job = self._jobs[job_id]
            folders = self._writes.get(job_id, frozenset())
            if folders & busy:
                continue
            self._queue.remove(job_id)
            if self._start(job):
                active.append(job)
                busy |= folders

    def _output_folder(self, op: Operation, job: Job) -> str:
        """Where a job saves its files: its outdir, else the configured output
        folder, else its first input's folder; for an operation that writes
        beside a path, its outdir, else that path's folder."""
        folder = job.params.get("outdir")
        if not folder and op.params.WRITES_BESIDE:
            folder = beside_folder(op, job.params)
        elif not folder:
            folder = self.default_outdir or os.path.dirname(first_input(job.params))
        return folder_key(folder) if folder else ""

    def _write_folders(self, op: Operation, job: Job) -> frozenset[str]:
        """Every folder a job may write in: its output folder, the folders of
        output files it names by path, and a folder given as its input (a
        report folder is written into, not beside)."""
        folders = {self._output_folder(op, job)}
        try:
            params = params_from_json(op, job.params)
        except RequestError:
            params = None
        if params is not None:
            folders.update(folder_key(os.fspath(f)) for f in params.write_folders())
        if op.params.WRITES_BESIDE and not job.params.get("outdir"):
            # A library folder given as the FASTA: it resolves to one in its
            # reference genomes folder.
            beside = beside_path(op, job.params)
            if is_local_folder(beside):
                folders.add(folder_key(reference_genomes_dir(beside)))
        for item in op.fields():
            value = job.params.get(item.name)
            if not isinstance(value, str) or not value:
                continue
            if item.spec.may_name_file and os.path.dirname(value):
                folders.add(folder_key(os.path.dirname(value)))
            elif (
                item.name in INPUT_NAMES
                and not is_network_path(value)
                and os.path.isdir(value)
            ):
                folders.add(folder_key(value))
        return frozenset(folders)

    def _start(self, job: Job) -> bool:
        """Start a job's worker; False if it could not start (job failed)."""
        request = {
            "op": job.op,
            "workspace_lease": self._workspace_leases[job.id].name,
            "params": job.params,
            "config": str(self.config_path) if self.config_path else None,
            "debug": self.debug,
            "allowed": self.allowed,
            # A server that saves nothing (read-only MCP) writes no card, whatever a job asks.
            "card": False
            if self.card is False
            else (job.card if job.card is not None else self.card),
        }
        line = json.dumps(request) + "\n"
        try:
            log = open(self.log_path(job.id), "a", encoding="utf-8", newline="\n")  # noqa: SIM115
        except OSError as e:
            self._finish(job, "failed", error=f"Could not open the job log: {e}")
            return False
        try:
            worker = subprocess.Popen(
                self.worker_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                encoding="utf-8",
                errors="replace",
                **detached(),
            )
        except OSError as e:
            log.close()
            self._finish(job, "failed", error=f"Could not start a worker: {e}")
            return False
        # The worker runs nothing until it has its request, and its follower
        # (which reaps it and frees its slot) waits until the request is
        # sent: until then, any failure stops and reaps the worker here.
        sent = threading.Event()
        try:
            job.status = "running"
            job.started = _now()
            self._save(job)
            self._workers[job.id] = worker
            threading.Thread(
                target=self._follow, args=(job, worker, log, sent), daemon=True
            ).start()
            assert worker.stdin is not None
            worker.stdin.write(line)
            worker.stdin.flush()
        except Exception as e:
            self._workers.pop(job.id, None)
            discard_worker(worker, CANCEL_GRACE_SECONDS)
            log.close()
            job.started = None
            logging.warning(f"Job {job.id} could not start: {e}")
            self._finish(job, "failed", error=f"Could not start the job: {e}")
            return False
        finally:
            sent.set()
        self._event(job, "status", status="running")
        return True

    def _follow(
        self,
        job: Job,
        worker: subprocess.Popen[str],
        log: Any,
        sent: threading.Event,
    ) -> None:
        """Read a worker's events until it exits (runs without the lock)."""
        sent.wait()
        with self._lock:
            if self._workers.get(job.id) is not worker:
                return  # It could not start: _start stopped and reaped it.
        assert worker.stdout is not None
        result: dict[str, Any] | None = None
        for line in worker.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                message = None
            if not isinstance(message, dict) or "event" not in message:
                log.write(line)
                log.flush()
                continue
            if message["event"] == "progress":
                with self._lock:
                    progress: JobProgress = {}
                    if message.get("stage") is not None:
                        progress["stage"] = message["stage"]
                    if message.get("message") is not None:
                        progress["message"] = message["message"]
                    if message.get("fraction") is not None:
                        progress["fraction"] = message["fraction"]
                    # Tools often run in many short steps with the same message.
                    if progress != job.progress:
                        job.progress = progress
                        self._event(job, "progress", **progress)
            elif message["event"] == "result":
                result = message.get("result")
        code = worker.wait()
        log.close()
        with self._lock:
            self._workers.pop(job.id, None)
            if worker.stdin:
                with contextlib.suppress(OSError):
                    worker.stdin.close()
            if isinstance(result, dict):
                status: Status = {
                    "ok": "succeeded",
                    "cancelled": "cancelled",
                }.get(str(result.get("status")), "failed")  # type: ignore[assignment]
                if self._stopping and status == "cancelled":
                    status = "interrupted"
                job.result = result
                job.exit_code = result.get("exit_code", code)
                self._finish(job, status, error_record=result.get("error"))
            else:
                job.exit_code = code
                cancelled = job.id in self._cancelling
                self._finish(
                    job,
                    "interrupted"
                    if self._stopping
                    else "cancelled"
                    if cancelled
                    else "failed",
                    error=(
                        "Stopped before writing a result."
                        if cancelled or self._stopping
                        else f"The worker exited ({code}) without a result; "
                        f"see GET /v1/jobs/{job.id}/log."
                    ),
                )
            self._cancelling.discard(job.id)
            self._pump()

    def _stop_worker(self, job_id: str, worker: subprocess.Popen[str]) -> None:
        """Ask a worker to cancel; kill it after the grace period (no lock)."""
        try:
            assert worker.stdin is not None
            worker.stdin.write("cancel\n")
            worker.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            worker.wait(CANCEL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            logging.warning(f"Job {job_id} did not stop; killing its worker.")
            kill_tree(worker.pid)

    def _finish(
        self,
        job: Job,
        status: Status,
        *,
        error: str | None = None,
        error_record: JobError | None = None,
    ) -> None:
        job.status = status
        job.finished = _now()
        if status == "cancelled":
            job.exit_code = CANCELLED_EXIT_CODE
        if error_record:
            job.error = error_record.copy()
        elif error:
            job.error = {
                "kind": "failure" if status == "failed" else "cancelled",
                "message": error,
            }
        self._writes.pop(job.id, None)
        try:
            self._event(job, "status", status=status)
            # The job is over whether or not it can be recorded yet: its slot and
            # folders are free already.
            self._unsaved[job.id] = None
            self._save_finished()
        finally:
            workspace = self._workspace_leases.pop(job.id, None)
            if workspace is not None:
                workspace.cleanup.close()

    def _save_finished(self) -> None:
        """Record finished jobs not yet saved; those saved may be evicted."""
        for job_id in list(self._unsaved):
            job = self._jobs[job_id]
            saved = True
            try:
                self._save(job)
            except OSError as e:
                saved = False
                logging.warning(f"Could not save job {job_id}'s record: {e}")
            try:
                with (
                    contextlib.closing(sqlite3.connect(self._history, timeout=0)) as db,
                    db,
                ):
                    self._index_summary(db, job)
            except (OSError, sqlite3.Error) as e:
                saved = False
                logging.warning(f"Could not list job {job_id}: {e}")
            if not saved:
                continue
            del self._unsaved[job_id]
            self._finished.append(job_id)
            if len(self._finished) > MAX_FINISHED_JOBS:
                del self._jobs[self._finished.popleft()]


def write_private_json(path: Path, data: Mapping[str, Any]) -> None:
    """Atomically publish ``data`` readable by this user only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=1)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
