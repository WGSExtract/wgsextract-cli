"""Local, resumable benchmark drivers for Linux and macOS. See developer_setup.md."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType, TracebackType
from typing import NoReturn


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _make_run_dir(path: Path) -> None:
    existing = path
    while not existing.exists():
        existing = existing.parent
    path.mkdir(parents=True, exist_ok=True)
    while path != existing:
        _sync_dir(path.parent)
        path = path.parent


def _publish(path: Path, text: str, *, durable: bool = False) -> None:
    pending = path.with_name(path.name + ".pending")
    with pending.open("w", encoding="utf-8") as stream:
        stream.write(text)
        if durable:
            stream.flush()
            os.fsync(stream.fileno())
    pending.replace(path)
    if durable:
        _sync_dir(path.parent)


class _Refused(SystemExit):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(code)
        self.message = message


def _abort(message: str, code: int = 65) -> NoReturn:
    print(message, file=sys.stderr)
    raise _Refused(code, message)


def _input(path: Path) -> Path:
    if not path.is_file() or not path.stat().st_size:
        _abort(f"Missing or empty local input: {path}", 66)
    return path.resolve()


def _m5(text: str) -> dict[str, str]:
    digests: dict[str, str] = {}
    for line in text.splitlines():
        fields = line.split("\t")
        if fields[0] != "@SQ":
            continue
        tags = dict(field.split(":", 1) for field in fields[1:] if ":" in field)
        name, digest = tags.get("SN", ""), tags.get("M5", "")
        if not name or name in digests or not re.fullmatch(r"[0-9a-fA-F]{32}", digest):
            _abort(f"Missing, invalid or duplicate @SQ identity: {name!r}")
        digests[name] = digest.lower()
    if not digests:
        _abort("No @SQ identities found")
    return digests


class LongRun:
    """Own one output directory; let failures propagate out of the context."""

    def __init__(self, root: Path, *, name: str, target: str) -> None:
        self.root = root.resolve()
        self.name = name
        self.target = target
        self.host = socket.gethostname()
        self.step = "preflight"
        self.log = self.root / "driver.log"
        self._defer_signal = False
        self._pending_signal = 0
        self._signals: dict[
            signal.Signals, int | Callable[[int, FrameType | None], object] | None
        ] = {}

    def __enter__(self) -> LongRun:
        try:
            _make_run_dir(self.root)
            self._lock = (self.root / ".lock").open("a")
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                self._lock.close()
                raise
        except BlockingIOError:
            _abort(f"Another driver holds the lock for {self.root}", 73)
        except OSError:
            self._alert("FAIL", 1)
            raise
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                self._signals[sig] = signal.signal(sig, self._interrupt)
            _publish(self.root / "driver.pid", f"{os.getpid()}\n")
            self._record("running")
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise
        return self

    def _interrupt(self, signum: int, _frame: FrameType | None) -> None:
        if self._defer_signal:
            self._pending_signal = signum
        else:
            raise SystemExit(128 + signum)

    def _close(self) -> None:
        try:
            (self.root / "driver.pid").unlink(missing_ok=True)
        except OSError as exc:
            print(f"Cannot remove benchmark pidfile: {exc}", file=sys.stderr)
        finally:
            self._lock.close()
            for sig, handler in self._signals.items():
                signal.signal(sig, handler)

    def _record(self, state: str) -> None:
        with (self.root / "driver.log").open("a", encoding="utf-8") as log:
            print(f"{_utc()} {state} step={self.step} log={self.log}", file=log)
        _publish(
            self.root / "handoff.md",
            f"# {self.name}\n\n- Updated: {_utc()}\n- Host: `{self.host}`\n"
            f"- State: {state}\n- Step: `{self.step}`\n- Log: `{self.log}`\n"
            "- Resume: inspect the log and rerun the same driver in this directory. "
            "Stages with `PASS` are skipped.\n",
        )

    def _coord(self, *args: str) -> None:
        subprocess.run(
            ["agent-coord", "--as", self.name, *args], check=True, timeout=30
        )

    def _alert(self, state: str, status: int) -> None:
        try:
            self._coord(
                "send",
                "--to",
                self.target,
                f"{self.name} {state} host={self.host} step={self.step} exit={status} log={self.log}",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Cannot send benchmark alert: {exc}", file=sys.stderr)

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        error: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        status = error.code if isinstance(error, SystemExit) else int(error is not None)
        status = 0 if status is None else status if isinstance(status, int) else 1
        state = "PASS" if status == 0 else "FAIL"
        self._defer_signal = True
        try:
            try:
                self._record(f"{state} exit={status}")
                if error is not None:
                    with self.log.open("a", encoding="utf-8") as log:
                        if isinstance(error, _Refused):
                            print(error.message, file=log)
                        elif not isinstance(error, SystemExit):
                            traceback.print_exception(error, file=log)
            except OSError as exc:
                print(f"Cannot write handoff: {exc}", file=sys.stderr)
            self._alert(state, status)
        finally:
            self._close()

    def hold(self, *lease_args: str) -> None:
        """Register this already-running PID; does not replace a capped `agent-coord run`."""
        self._coord(
            "hold", "--label", self.name, "--pid", str(os.getpid()), *lease_args
        )

    def check_sha256(self, path: Path, expected: str) -> None:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            _abort(f"Invalid SHA-256 pin for {path}")
        digest = hashlib.sha256()
        with _input(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected.lower():
            _abort(f"SHA-256 mismatch: {path} (got {digest.hexdigest()})")

    def check_reference(
        self,
        cram: Path,
        reference: Path,
        *,
        dictionary: bool = False,
        samtools: str = "samtools",
    ) -> None:
        """Compare local CRAM header identities with a FASTA or a pinned SAM dictionary."""
        cram, reference = _input(cram), _input(reference)
        header = subprocess.check_output(
            [samtools, "view", "-H", str(cram)], text=True, timeout=60
        )
        ref = (
            reference.read_text(encoding="utf-8")
            if dictionary
            else subprocess.check_output(
                [samtools, "dict", str(reference)], text=True, timeout=600
            )
        )
        expected = _m5(ref)
        for name, digest in _m5(header).items():
            if expected.get(name) != digest:
                _abort(f"Reference M5 mismatch for {name}: {cram} vs {reference}")

    def require_slice(self, root: Path) -> None:
        """Gate the full driver on a separately measured `slice` stage."""
        if not (root / "slice" / "PASS").is_file():
            _abort(f"No successful sizing slice in {root}; run and review it first")

    def stage(self, name: str, command: Sequence[str]) -> None:
        """Run a foreground command; it must validate outputs before returning zero."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or name in {
            "driver.log",
            "driver.pid",
            "handoff.md",
        }:
            _abort(f"Invalid stage name: {name!r}", 64)
        self.step = name
        directory = self.root / name
        directory.mkdir(exist_ok=True)
        marker = directory / "PASS"
        self.log = directory / "stage.log"
        if marker.is_file():
            self._record("skipped (PASS)")
            return
        self._record("running")
        with self.log.open("a", encoding="utf-8") as log:
            print(f"\n{_utc()} attempt", file=log, flush=True)
            start = time.monotonic()
            status, rss = self._measure(command, log.fileno())
            wall = time.monotonic() - start
            os.fsync(log.fileno())
        _sync_dir(self.root)
        _publish(
            directory / "metrics.json",
            json.dumps(
                {
                    "exit_code": status,
                    "wall_seconds": wall,
                    "peak_rss_kib": rss,
                }
            )
            + "\n",
            durable=True,
        )
        if status:
            raise SystemExit(status)
        _publish(marker, f"{_utc()}\n", durable=True)
        self._record("stage passed")

    def _measure(self, command: Sequence[str], output: int) -> tuple[int, int]:
        child = None
        status = None
        try:
            # A signal during Popen must not strand a child whose PID we do not yet know.
            self._defer_signal = True
            child = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
                pass_fds=(self._lock.fileno(),),
            )
            self._defer_signal = False
            if self._pending_signal:
                raise SystemExit(128 + self._pending_signal)
            _, raw, usage = os.wait4(child.pid, 0)
            status = child.returncode = os.waitstatus_to_exitcode(raw)
        finally:
            self._defer_signal = True
            try:
                if child is not None and status != 0:
                    # A failed leader may already be reaped while its workers are still alive.
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
            finally:
                self._defer_signal = False
        if self._pending_signal:
            raise SystemExit(128 + self._pending_signal)
        rss = usage.ru_maxrss
        if sys.platform == "darwin":
            rss = (rss + 1023) // 1024
        return 128 - status if status < 0 else status, rss
