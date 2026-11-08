#!/usr/bin/env python3
"""Measure the energy that a command uses on Apple Silicon, with powermetrics.

  power.py check --cmd CMD
      Take one sample with CMD (for example "sudo -n powermetrics") and exit 0
      if it reports the CPU's and GPU's energy.
  power.py run --cmd CMD --out DIR [--interval MS] [--idle SECONDS]
               [--quiet WATTS] [--quiet-wait SECONDS] -- COMMAND [ARG...]
      Sample power with CMD every MS milliseconds (default 1000). Measure the
      idle power over the last SECONDS (default 60) before COMMAND starts; with
      --quiet, first wait (up to --quiet-wait, default 1800) for a window whose
      mean package power is at most WATTS. Then run COMMAND with this process's
      standard input, output and error, write DIR/power.txt (key=value) and
      DIR/power-samples.tsv, and exit with COMMAND's status (128 + the signal
      if it was killed).

Each powermetrics sample reports the energy that the CPU, GPU and ANE used
since the last one (mJ). A sample's interval ends when it arrives, so the
energy of a time window is the sum of its samples, each weighted by the share
of its interval inside the window. The package is CPU + GPU + ANE
(powermetrics' "Combined Power"); it leaves out memory, storage and the
display. The net energy subtracts the idle power over the run's time.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

SAMPLERS = "cpu_power,gpu_power"
MJ_PER_WH = 3.6e6


@dataclass(frozen=True)
class Sample:
    """The energy (mJ) used in the interval of `seconds` that ended at `end`."""

    end: float
    seconds: float
    cpu_mj: float
    gpu_mj: float
    ane_mj: float

    @property
    def start(self) -> float:
        return self.end - self.seconds


@dataclass(frozen=True)
class Energy:
    """Energy (mJ) over a window, and how many of its seconds samples covered."""

    cpu_mj: float = 0.0
    gpu_mj: float = 0.0
    ane_mj: float = 0.0
    covered_s: float = 0.0

    @property
    def package_mj(self) -> float:
        return self.cpu_mj + self.gpu_mj + self.ane_mj

    def watts(self) -> tuple[float, float, float]:
        """Mean CPU, GPU and package power (W) over the covered seconds."""
        if self.covered_s <= 0:
            return (0.0, 0.0, 0.0)
        scale = 1000 * self.covered_s
        return (self.cpu_mj / scale, self.gpu_mj / scale, self.package_mj / scale)


class PlistStream:
    """Splits powermetrics' `-f plist` output into its NUL-separated plists."""

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer += data
        *documents, self._buffer = self._buffer.split(b"\0")
        return [plist for plist in map(_load, documents) if plist is not None]

    def finish(self) -> list[dict[str, Any]]:
        documents, self._buffer = [self._buffer], b""
        return [plist for plist in map(_load, documents) if plist is not None]


def _load(document: bytes) -> dict[str, Any] | None:
    document = document.strip()
    if not document:
        return None
    plist = plistlib.loads(document)
    return plist if isinstance(plist, dict) else None


def to_sample(plist: dict[str, Any], end: float) -> Sample:
    """The sample in one powermetrics plist, whose interval ended at `end`."""
    seconds = float(plist["elapsed_ns"]) / 1e9
    processor = plist.get("processor")
    if not isinstance(processor, dict):
        raise ValueError("the sample has no processor energy (needs cpu_power)")

    def energy(name: str) -> float:
        # Energy counters (mJ) where powermetrics has them, else mean power (mW).
        if f"{name}_energy" in processor:
            return float(processor[f"{name}_energy"])
        if f"{name}_power" in processor:
            return float(processor[f"{name}_power"]) * seconds
        if name == "cpu":
            raise ValueError("the sample has no CPU energy")
        return 0.0

    return Sample(end, seconds, energy("cpu"), energy("gpu"), energy("ane"))


def integrate(samples: Iterable[Sample], start: float, end: float) -> Energy:
    """The energy of the window [start, end], prorating partly covered samples."""
    cpu = gpu = ane = covered = 0.0
    for sample in samples:
        overlap = min(sample.end, end) - max(sample.start, start)
        if overlap <= 0 or sample.seconds <= 0:
            continue
        share = overlap / sample.seconds
        cpu += sample.cpu_mj * share
        gpu += sample.gpu_mj * share
        ane += sample.ane_mj * share
        covered += overlap
    return Energy(cpu, gpu, ane, covered)


def report(
    run: Energy,
    run_s: float,
    idle: Energy | None,
) -> dict[str, str]:
    """power.txt's energy fields: gross Wh, mean W, and Wh above the idle power."""
    coverage = run.covered_s / run_s if run_s > 0 else 0.0
    # Uncovered seconds (a late first or last sample) count at the run's mean power.
    scale = 1 / coverage if coverage > 0 else 0.0
    gross = {
        "cpu": run.cpu_mj * scale / MJ_PER_WH,
        "gpu": run.gpu_mj * scale / MJ_PER_WH,
        "ane": run.ane_mj * scale / MJ_PER_WH,
        "package": run.package_mj * scale / MJ_PER_WH,
    }
    fields = {
        "run_s": f"{run_s:.3f}",
        "coverage": f"{coverage:.4f}",
        "mean_package_w": f"{gross['package'] * 3600 / run_s:.3f}" if run_s else "-",
    }
    fields.update({f"{name}_wh": f"{wh:.5f}" for name, wh in gross.items()})
    idle_watts: dict[str, float] = {}
    if idle:
        idle_watts["cpu"], idle_watts["gpu"], idle_watts["package"] = idle.watts()
    for name in ("cpu", "gpu", "package"):
        if idle and idle.covered_s > 0:
            fields[f"idle_{name}_w"] = f"{idle_watts[name]:.3f}"
            net = gross[name] - idle_watts[name] * run_s / 3600
            fields[f"net_{name}_wh"] = f"{net:.5f}"
        else:
            fields[f"idle_{name}_w"] = "-"
            fields[f"net_{name}_wh"] = "-"
    return fields


class Sampler:
    """Runs a powermetrics command and collects its samples as they arrive."""

    def __init__(self, command: Sequence[str], interval_ms: int, log: IO[bytes]):
        argv = [*command, "--samplers", SAMPLERS, "-i", str(interval_ms), "-f", "plist"]
        self.samples: list[Sample] = []
        self.error: str | None = None
        self._changed = threading.Condition()
        self._stopping = threading.Event()
        self._process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        stream = PlistStream()
        assert self._process.stdout is not None
        fd = self._process.stdout.fileno()
        try:
            while not self._stopping.is_set():
                data = os.read(fd, 1 << 16)
                arrived = time.monotonic()
                plists = stream.feed(data) if data else stream.finish()
                with self._changed:
                    self.samples.extend(to_sample(plist, arrived) for plist in plists)
                    self._changed.notify_all()
                if not data:
                    break
        except (ValueError, plistlib.InvalidFileException, KeyError) as error:
            self.error = f"cannot read powermetrics' output: {error}"
        finally:
            # Closing the pipe also stops powermetrics when a wrapper does not
            # forward signals to it: its next write fails.
            self._process.stdout.close()
            with self._changed:
                self._stopping.set()
                self._changed.notify_all()

    def wait_for(self, done: Callable[[list[Sample]], bool], timeout: float) -> bool:
        """Wait until done(samples) is true; false on timeout or when sampling ends."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while not done(self.samples):
                left = deadline - time.monotonic()
                if left <= 0 or self._stopping.is_set():
                    return bool(done(self.samples))
                self._changed.wait(left)
            return True

    def wait_for_next(self, timeout: float) -> bool:
        count = len(self.samples)
        return self.wait_for(lambda samples: len(samples) > count, timeout)

    def stop(self) -> None:
        self._stopping.set()
        self._process.terminate()
        try:
            self._process.wait(10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._reader.join(10)


def parse_command(text: str) -> list[str]:
    command = shlex.split(text)
    if not command:
        raise SystemExit("power.py: --cmd is empty")
    if shutil.which(command[0]) is None:
        raise SystemExit(f"power.py: {command[0]} is not installed")
    return command


def check(args: argparse.Namespace) -> int:
    command = parse_command(args.cmd)
    argv = [*command, "--samplers", SAMPLERS, "-i", "200", "-n", "1", "-f", "plist"]
    try:
        result = subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        print(f"power.py: {args.cmd} gave no sample in 60 s", file=sys.stderr)
        return 1
    stream = PlistStream()
    try:
        samples = [to_sample(p, 0.0) for p in stream.feed(result.stdout)]
        samples += [to_sample(p, 0.0) for p in stream.finish()]
    except (ValueError, plistlib.InvalidFileException, KeyError) as error:
        samples, problem = [], str(error)
    else:
        problem = "no samples"
    if result.returncode != 0 or not samples:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        reason = detail[-1] if detail else problem
        print(f"power.py: {args.cmd} cannot sample power: {reason}", file=sys.stderr)
        return 1
    cpu, gpu, package = Energy(
        samples[0].cpu_mj, samples[0].gpu_mj, samples[0].ane_mj, samples[0].seconds
    ).watts()
    print(f"power: CPU {cpu:.2f} W, GPU {gpu:.2f} W, package {package:.2f} W")
    return 0


def run(args: argparse.Namespace) -> int:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("power.py: run needs a command after --")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    interval = args.interval / 1000
    fields: dict[str, str] = {"cmd": args.cmd, "interval_ms": str(args.interval)}
    with open(out / "power.log", "wb") as log:
        sampler = Sampler(parse_command(args.cmd), args.interval, log)
        try:
            # The first sample covers powermetrics' start-up; the idle window follows it.
            if not sampler.wait_for(lambda s: len(s) >= 2, 30 + 2 * interval):
                print(
                    f"power.py: {args.cmd} failed to give samples: "
                    f"{sampler.error or 'see power.log'}",
                    file=sys.stderr,
                )
                return 2
            first = sampler.samples[0].end
            waited_from = time.monotonic()
            quiet = True
            idle: Energy | None = None
            while args.idle > 0:
                sampler.wait_for(
                    lambda s: s[-1].end >= first + args.idle, args.idle + 30
                )
                last = sampler.samples[-1].end
                idle = integrate(sampler.samples, last - args.idle, last)
                if args.quiet is None or idle.watts()[2] <= args.quiet:
                    break
                if time.monotonic() - waited_from > args.quiet_wait:
                    quiet = False
                    break
                sampler.wait_for_next(30 + 2 * interval)
            started = time.monotonic()
            if idle is not None:
                idle = integrate(sampler.samples, started - args.idle, started)
            fields["quiet_wait_s"] = f"{started - waited_from - args.idle:.1f}"
            fields["quiet"] = "1" if quiet else "0"
            fields["load1"] = f"{os.getloadavg()[0]:.2f}"
            rc = run_command(command)
            ended = time.monotonic()
            sampler.wait_for(lambda s: s[-1].end >= ended, 5 + 5 * interval)
        finally:
            sampler.stop()
    samples = sampler.samples
    energy = integrate(samples, started, ended)
    fields.update(report(energy, ended - started, idle))
    fields["idle_s"] = f"{idle.covered_s:.1f}" if idle else "0"
    fields["samples"] = str(
        sum(1 for s in samples if s.end > started and s.start < ended)
    )
    fields["status"] = "ok"
    if sampler.error or energy.covered_s < 0.95 * (ended - started):
        fields["status"] = "incomplete"
    elif not quiet:
        fields["status"] = "not-quiet"
    with open(out / "power.txt", "w") as file:
        file.writelines(f"{key}={value}\n" for key, value in fields.items())
    with open(out / "power-samples.tsv", "w") as file:
        file.write("end_s\tseconds\tcpu_mj\tgpu_mj\tane_mj\n")
        for s in samples:
            file.write(
                f"{s.end - started:.3f}\t{s.seconds:.4f}\t{s.cpu_mj:.0f}\t"
                f"{s.gpu_mj:.0f}\t{s.ane_mj:.0f}\n"
            )
    if sampler.error:
        print(f"power.py: {sampler.error}", file=sys.stderr)
    return rc


def run_command(command: Sequence[str]) -> int:
    """Run the command in the foreground, passing on SIGTERM, SIGINT and SIGHUP."""
    child = subprocess.Popen(command)
    forwarded = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    previous = {
        signo: signal.signal(signo, lambda signo, _: child.send_signal(signo))
        for signo in forwarded
    }
    try:
        rc = child.wait()
    finally:
        for signo, handler in previous.items():
            signal.signal(signo, handler)
    return 128 - rc if rc < 0 else rc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="action", required=True)
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--cmd", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--cmd", required=True)
    run_parser.add_argument("--out", required=True)
    run_parser.add_argument("--interval", type=int, default=1000)
    run_parser.add_argument("--idle", type=float, default=60)
    run_parser.add_argument("--quiet", type=float)
    run_parser.add_argument("--quiet-wait", type=float, default=1800)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.action == "check":
        return check(args)
    if args.interval < 100 or args.idle < 0:
        parser.error("--interval must be at least 100 and --idle at least 0")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
