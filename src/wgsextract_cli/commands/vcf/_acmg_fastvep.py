"""Manage the fastVEP subprocess pipeline for ACMG annotation."""

from __future__ import annotations

import contextlib
import logging
import subprocess
from collections import deque
from collections.abc import Callable
from typing import IO

from wgsextract_cli.core.execution_context import context_thread
from wgsextract_cli.core.process_registry import terminate_trees
from wgsextract_cli.core.utils import WGSExtractError, _is_sigpipe, popen

_SETUP_DONE = "Using --pick order"


class _FastVep:
    """fastVEP reading the input (minus CSQ) from bcftools; stderr is read
    until its setup is done, then drained by a thread."""

    def __init__(self, command: list[str], strip: list[str], env: dict[str, str]):
        self._processes: list[subprocess.Popen[str]] = []
        self._join_drains: dict[IO[str], Callable[[float], None]] = {}
        # Others the run starts and close() must stop: the reader and writer.
        self.others: list[subprocess.Popen[str]] = []
        try:
            self._start(command, strip, env)
        except BaseException:
            self.close()
            raise

    def _start(self, command: list[str], strip: list[str], env: dict[str, str]) -> None:
        self.strip = popen(
            strip, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self._processes.append(self.strip)
        self.proc = popen(
            command,
            stdin=self.strip.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self._processes.insert(0, self.proc)
        assert self.strip.stdout is not None and self.proc.stderr is not None
        self.strip.stdout.close()  # fastVEP holds the pipe now
        self.tail: deque[str] = deque(maxlen=40)
        self.strip_tail: deque[str] = deque(maxlen=10)
        assert self.strip.stderr is not None
        strip_err = self.strip.stderr
        self._strip_drain = context_thread(
            target=self._read_strip_stderr, args=(strip_err,), daemon=True
        )
        self._strip_drain.start()
        self._join_drains[strip_err] = self._strip_drain.join
        self.setup: list[str] = []
        self.ready = False
        for line in self.proc.stderr:
            line = line.rstrip("\n")
            self.setup.append(line)
            logging.debug("fastVEP: %s", line)
            if line.startswith(_SETUP_DONE):
                self.ready = True
                break
        stderr = self.proc.stderr
        self._drain = context_thread(target=self._read, args=(stderr,), daemon=True)
        self._drain.start()
        self._join_drains[stderr] = self._drain.join

    def _read_strip_stderr(self, stderr: IO[str]) -> None:
        with stderr:
            self.strip_tail.extend(stderr)

    def _read(self, stderr: IO[str]) -> None:
        with stderr:
            for line in stderr:
                line = line.rstrip("\n")
                self.tail.append(line)
                if line.startswith("warning:"):
                    logging.warning("fastVEP %s", line)

    @property
    def stdout(self) -> IO[str]:
        assert self.proc.stdout is not None
        return self.proc.stdout

    def close(self) -> None:
        processes = (*self._processes, *self.others)
        try:
            terminate_trees([proc for proc in processes if proc.poll() is None])
            for proc in processes:
                proc.wait()
        finally:
            for proc in processes:
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream is not None:
                        if join := self._join_drains.get(stream):
                            join(5)
                        else:
                            with contextlib.suppress(OSError):
                                stream.close()

    def require_ready(self) -> None:
        """fastVEP stopped before its setup was done: say why."""
        if not self.ready:
            self.proc.wait()
            detail = "\n".join(self.setup).strip()
            raise WGSExtractError(f"fastVEP failed before reading the input: {detail}")

    def check(self) -> None:
        self._drain.join(timeout=30)
        self._strip_drain.join(timeout=30)
        self._raise_failure(self.strip.wait(), self.proc.wait())

    def check_exited(self) -> None:
        """After the merge failed: raise the tools' error if one of them
        failed (fastVEP stopping makes the merge fail too), else return."""
        try:
            fastvep = self.proc.wait(timeout=5)
            strip = self.strip.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return
        self._drain.join(timeout=5)
        self._strip_drain.join(timeout=5)
        self._raise_failure(strip, fastvep)

    def _raise_failure(self, strip: int, fastvep: int) -> None:
        detail = "".join(self.strip_tail).strip()
        # Windows reports a failed stdout write instead of SIGPIPE when fastVEP
        # stops. Preserve genuine input errors, but report the downstream cause.
        broken_pipe = _is_sigpipe(strip) or any(
            line.strip() == "[main_vcfannotate] Error: failed to write to -"
            for line in detail.splitlines()
        )
        if strip != 0 and not (broken_pipe and fastvep != 0):
            raise WGSExtractError(f"bcftools failed reading the input: {detail}")
        if fastvep != 0:
            detail = "\n".join([*self.setup[-10:], *self.tail]).strip()
            raise WGSExtractError(f"fastVEP failed: {detail}")
