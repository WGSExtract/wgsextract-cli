"""Stderr-only progress; never intercept a tool's data stream."""

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping

_READS = re.compile(r"(?:Processed|mapped)\s+(\d+)\s+(?:reads|sequences)", re.I)


class Heartbeat:
    def __init__(
        self,
        label: str,
        total: int | None = None,
        unit: str = "reads",
        *,
        interval: float = 60,
        scan: Callable[[], None] | None = None,
        emit: bool = True,
    ) -> None:
        self.label, self.total, self.unit = label, total, unit
        self.interval, self.scan = interval, scan
        self.emit = emit
        self.completed = 0
        self.started = time.monotonic()
        self._estimate: tuple[float, float] | None = None
        # (time, share written) at the first sample after the input was read.
        self._write_start: tuple[float, float] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "Heartbeat":
        self.report()
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self._scan()
        self.report()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self._scan()
            self.report()

    def _scan(self) -> None:
        if self.scan:
            try:
                self.scan()
            except (OSError, ValueError) as error:
                logging.warning("Progress scan unavailable: %s", error)
                self.scan = None

    def advance(self, count: int = 1) -> None:
        with self._lock:
            self.completed += count

    def read_line(self, line: str) -> None:
        if match := _READS.search(line):
            self.advance(int(match.group(1)))

    def estimate(self, read: float, written: float = 0.0) -> None:
        """Record the shares of the input read and of the output written, for
        a pass that reports its count only when it finishes. A reported count
        takes precedence."""
        with self._lock:
            if read >= 1 and self._write_start is None:
                self._write_start = (time.monotonic(), min(1.0, max(0.0, written)))
            self._estimate = (min(1.0, max(0.0, read)), min(1.0, max(0.0, written)))

    def _report_estimate(self, read: float, written: float) -> None:
        total = self.total
        if written:
            count = (
                f"~{round(written * total):,}/{total:,} {self.unit} written"
                if total
                else f"{written:.0%} written"
            )
        else:
            count = f"{read:.0%} of the input read"
        _report(count, (read + written) / 2)
        if not self.emit:
            return
        now = time.monotonic()
        detail = f" ({written:.1%})" if written and total else ""
        if read >= 1 and not written:
            detail = ", collating pairs to write"
        elif not written and 0 < read < 1:
            left = (now - self.started) * (1 - read) / read
            detail += f", about {left:.0f}s left to read; output is written after that"
        elif written < 1 and self._write_start:
            # The rate comes from the change since writing was first seen, so
            # output already written by then isn't credited to too little time.
            since, base = self._write_start
            if written > base and now > since:
                left = (now - since) * (1 - written) / (written - base)
                detail += f", about {left:.0f}s left to write"
        logging.info(
            "%s heartbeat: %s%s, elapsed %.0fs",
            self.label,
            count,
            detail,
            max(0.0, now - self.started),
        )

    def report(self) -> None:
        with self._lock:
            done = self.completed
            estimate = None if done else self._estimate
        if estimate:
            self._report_estimate(*estimate)
            return
        if done or self.total:
            _report(
                f"{done:,}/{self.total:,} {self.unit}"
                if self.total
                else f"{done:,} {self.unit}",
                min(1.0, done / self.total) if self.total else None,
            )
        if not self.emit:
            return
        elapsed = max(0.0, time.monotonic() - self.started)
        progress = (
            f"{done:,} {self.unit}"
            if done or self.total
            else "running; processed-read count not yet available"
        )
        if self.total:
            progress = f"{done:,}/{self.total:,} {self.unit} ({min(100, done / self.total * 100):.1f}%)"
        eta = ""
        if self.total and 0 < done < self.total:
            eta = f", estimated {elapsed * (self.total - done) / done:.0f}s remaining"
        if self.total and done >= self.total and self.unit == "reads":
            eta = ", aligner input processed; downstream stages may still be running"
        logging.info(
            "%s heartbeat: %s, elapsed %.0fs%s", self.label, progress, elapsed, eta
        )


class ContigProgress:
    """Reports each finished contig of a run split into per-contig workers as
    a ``contig`` progress event. The fraction is the share of the contigs'
    bases done, or of the contigs when their lengths are unknown."""

    def __init__(
        self, contigs: Iterable[str], lengths: Mapping[str, int] | None = None
    ) -> None:
        names = list(contigs)
        self._sizes = {name: (lengths[name] if lengths else 1) for name in names}
        self._total = sum(self._sizes.values())
        self._finished = self._done = 0
        self._lock = threading.Lock()

    def done(self, contig: str) -> None:
        from wgsextract_cli.api._runtime import report

        # Under the lock, so events arrive in order and fractions only grow.
        with self._lock:
            self._finished += 1
            self._done += self._sizes[contig]
            report(
                "contig",
                f"{contig} ({self._finished}/{len(self._sizes)} contigs)",
                self._done / self._total if self._total else None,
            )


def _report(count: str, fraction: float | None) -> None:
    # The CLI renders heartbeat events on its progress line (cli/dispatch).
    from wgsextract_cli.api._runtime import report

    report("heartbeat", count, fraction)
