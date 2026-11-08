"""native/bench/power.py: energy of a command from powermetrics samples (#547)."""

from __future__ import annotations

import shlex
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the aligner benchmarks run on macOS"
)

POWER = Path(__file__).resolve().parents[1] / "native" / "bench" / "power.py"

# Stands in for powermetrics, which needs root: emits `-f plist` samples every
# `-i` ms whose CPU power is 3 W while the marker file exists, and 1 W otherwise,
# with a constant 0.5 W GPU. Options before powermetrics' own: "stop=N" exits
# after N samples, "nogpu" leaves out the GPU fields.
FAKE_POWERMETRICS = textwrap.dedent(
    """
    import plistlib, sys, time, os
    marker = sys.argv[1]
    options = sys.argv[2:sys.argv.index("--samplers")]
    stop = [int(o[5:]) for o in options if o.startswith("stop=")]
    interval = int(sys.argv[sys.argv.index("-i") + 1]) / 1000
    count = int(sys.argv[sys.argv.index("-n") + 1]) if "-n" in sys.argv else None
    last = time.monotonic()
    if stop:
        count = stop[0]
    while count is None or count > 0:
        time.sleep(interval)
        now = time.monotonic()
        seconds, last = now - last, now
        cpu_mw = 3000 if os.path.exists(marker) else 1000
        processor = {"cpu_energy": round(cpu_mw * seconds), "ane_energy": 0}
        if "nogpu" not in options:
            processor["gpu_energy"] = round(500 * seconds)
        sample = {"elapsed_ns": int(seconds * 1e9), "processor": processor}
        sys.stdout.buffer.write(plistlib.dumps(sample) + b"\\0")
        sys.stdout.flush()
        count = None if count is None else count - 1
    """
)


def fake_powermetrics(tmp_path: Path, *options: str) -> tuple[str, Path]:
    script = tmp_path / "fake_powermetrics.py"
    script.write_text(FAKE_POWERMETRICS)
    marker = tmp_path / "busy"
    return shlex.join([sys.executable, str(script), str(marker), *options]), marker


def read_report(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def test_run_reports_energy_above_the_idle_baseline(tmp_path: Path) -> None:
    cmd, marker = fake_powermetrics(tmp_path)
    # The command draws 2 W more CPU power for 1 s, prints, and fails with 3.
    command = (
        f"import pathlib, time; m = pathlib.Path({str(marker)!r}); m.touch(); "
        "time.sleep(1.0); m.unlink(); print('alignments'); raise SystemExit(3)"
    )
    out = tmp_path / "run"
    result = subprocess.run(
        [sys.executable, str(POWER), "run", "--cmd", cmd, "--interval", "100"]
        + ["--idle", "0.5", "--out", str(out), "--", sys.executable, "-c", command],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 3
    assert result.stdout == "alignments\n"
    report = read_report(out / "power.txt")
    assert report["status"] == "ok"
    assert float(report["idle_cpu_w"]) == pytest.approx(1.0, rel=0.05)
    assert float(report["idle_package_w"]) == pytest.approx(1.5, rel=0.05)
    run_s = float(report["run_s"])
    # Gross energy is everything drawn during the run, net only the 2 W extra.
    gross_package_w = float(report["package_wh"]) * 3600 / run_s
    assert gross_package_w > 2.5
    assert float(report["net_cpu_wh"]) * 3600 == pytest.approx(2.0, rel=0.3)
    assert float(report["net_gpu_wh"]) * 3600 == pytest.approx(0.0, abs=0.05)


def test_check_reports_a_sampler_that_cannot_run(tmp_path: Path) -> None:
    cmd, _ = fake_powermetrics(tmp_path)
    working = subprocess.run(
        [sys.executable, str(POWER), "check", "--cmd", cmd],
        capture_output=True,
        text=True,
        timeout=60,
    )
    failing = subprocess.run(
        [
            sys.executable,
            str(POWER),
            "check",
            "--cmd",
            shlex.join([sys.executable, "-c", "exit(1)"]),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert working.returncode == 0
    assert "CPU 1.00 W" in working.stdout
    assert failing.returncode == 1
    assert "cannot sample power" in failing.stderr


def test_sampler_that_stops_while_waiting_for_quiet_does_not_hold_up_the_run(
    tmp_path: Path,
) -> None:
    # PR #565 review: an exited sampler made the quiet wait spin for 30 minutes.
    cmd, _ = fake_powermetrics(tmp_path, "stop=5")
    out = tmp_path / "run"
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(POWER), "run", "--cmd", cmd, "--interval", "100"]
        + ["--idle", "0.2", "--quiet", "0", "--out", str(out), "--"]
        + [sys.executable, "-c", "print('alignments')"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert time.monotonic() - started < 30
    assert result.returncode == 0
    assert result.stdout == "alignments\n"
    assert "running without power data" in result.stderr
    report = read_report(out / "power.txt")
    assert report["status"] == "failed"
    assert "stopped" in report["error"]
    assert "package_wh" not in report


def test_check_rejects_a_sampler_without_gpu_power(tmp_path: Path) -> None:
    cmd, _ = fake_powermetrics(tmp_path, "nogpu")
    result = subprocess.run(
        [sys.executable, str(POWER), "check", "--cmd", cmd],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert "no GPU energy" in result.stderr
