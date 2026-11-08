"""Stderr-only progress; never intercept a tool's data stream."""

import contextlib
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping

_READS = re.compile(r"(?:Processed|mapped)\s+(\d+)\s+(?:reads|sequences)", re.I)
# Aligners report reads per batch; a batch of ultra-long ONT reads from a
# centromeric satellite can take hours, so a quiet aligner is not news before this.
STALL_AFTER = 15 * 60
# Below this many busy cores a quiet aligner is waiting, not aligning.
IDLE_CORES = 0.05


def process_cpu_seconds(pid: int) -> float | None:
    """CPU time used so far by a process and its children (a wrapper's tool)."""
    import psutil

    try:
        process = psutil.Process(pid)
        total = 0.0
        for each in [process, *process.children(recursive=True)]:
            with contextlib.suppress(psutil.Error):
                times = each.cpu_times()
                total += float(times.user + times.system)
        return total
    except psutil.Error:
        return None


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
        worker: tuple[str, Callable[[], float | None]] | None = None,
        stall_after: float = STALL_AFTER,
    ) -> None:
        """``worker`` names the process doing the counted work and reads its
        CPU seconds, so a long wait for the next count says whether it is
        still working or stalled."""
        self.label, self.total, self.unit = label, total, unit
        self.interval, self.scan = interval, scan
        self.emit = emit
        self.worker, self.stall_after = worker, stall_after
        self.completed = 0
        self.started = self._progressed = time.monotonic()
        self._cpu: tuple[float, float] | None = None
        self._busy = self.started
        self._warned: str | None = None
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
            if count:
                self._progressed = self._busy = time.monotonic()
                self._warned = None

    def read_line(self, line: str) -> None:
        if match := _READS.search(line):
            self.advance(int(match.group(1)))

    def report(self) -> None:
        with self._lock:
            done = self.completed
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
            "%s heartbeat: %s, elapsed %.0fs%s%s",
            self.label,
            progress,
            elapsed,
            eta,
            self._activity(),
        )

    def _activity(self) -> str:
        """Once no count has arrived for ``stall_after`` seconds: whether the
        worker is still using CPU (working on a slow batch) or has been idle as
        long (stalled). Warns once per quiet spell and state."""
        if self.worker is None:
            return ""
        name, cpu_seconds = self.worker
        now, used = time.monotonic(), cpu_seconds()
        if used is None:
            return ""
        previous, self._cpu = self._cpu, (now, used)
        cores = (
            max(0.0, used - previous[1]) / max(now - previous[0], 1e-9)
            if previous
            else 0.0
        )
        with self._lock:
            if previous is None or cores >= IDLE_CORES:
                self._busy = now
            quiet, idle = now - self._progressed, now - self._busy
        if quiet < self.stall_after:
            return ""
        state = "stalled" if idle >= self.stall_after else "busy"
        if self._warned != state:
            self._warned = state
            if state == "busy":
                logging.warning(
                    "%s has reported no progress for %.0f min but is using %.1f "
                    "CPU cores, so it is still working: a batch of long reads "
                    "from centromeres or satellite repeats can take hours.",
                    name,
                    quiet / 60,
                    cores,
                )
            else:
                from wgsextract_cli.core.results import warn

                warn(
                    "aligner_stalled",
                    f"{name} has reported no progress and used no CPU for "
                    f"{idle / 60:.0f} min: it is waiting on its input or output. "
                    "Check the input's disk or network and the stages after it.",
                    tool=name,
                    minutes=round(idle / 60),
                )
        return (
            f"; {name} {state}: no progress for {quiet / 60:.0f} min, "
            f"{cores:.1f} CPU cores in use"
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
