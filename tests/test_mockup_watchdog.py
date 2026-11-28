"""Mockup watchdog recovery and outage notifications."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "gui/mockups/hosting/watchdog.sh"
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="The systemd watchdog runs on POSIX hosts"
)


@dataclass
class Watchdog:
    root: Path

    def command(self, now: int) -> list[str]:
        return [
            str(SCRIPT),
            "--host",
            "unreachable.invalid",
            "--url",
            "http://review-host.invalid:8765/api/health",
            "--retry-delay",
            "0",
            "--agent-coord",
            str(self.root / "bin" / "coordinator"),
            "--state-file",
            str(self.root / "state" / "watchdog"),
            "--reminder-interval",
            "3600",
            "--now",
            str(now),
        ]

    def run(
        self, *, now: int, first: str = "down", retry: str = "down", ssh: int = 0
    ) -> int:
        (self.root / "first").write_text(first)
        (self.root / "retry").write_text(retry)
        (self.root / "ssh-status").write_text(str(ssh))
        (self.root / "phase").write_text("first")
        bin_dir = self.root / "bin"
        result = subprocess.run(
            self.command(now),
            env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.defpath},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert not result.stderr, result.stderr
        return result.returncode

    def messages(self) -> list[str]:
        alerts = self.root / "alerts"
        return alerts.read_text().splitlines() if alerts.exists() else []


@pytest.fixture
def watchdog(tmp_path: Path) -> Watchdog:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    root = shlex.quote(str(tmp_path))
    commands = {
        "ssh": f'exit "$(cat {root}/ssh-status)"\n',
        "curl": (
            f"phase=$(cat {root}/phase)\n"
            f'[ "$phase" = first ] && printf retry > {root}/phase\n'
            f'[ "$(cat {root}/"$phase")" = up ] || exit 22\n'
            "echo '{\"ok\": true}'\n"
        ),
        "coordinator": f"printf '%s\\n' \"$4\" >> {root}/alerts\n",
    }
    for name, body in commands.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\nset -eu\n" + body)
        path.chmod(0o755)
    return Watchdog(tmp_path)


@pytest.mark.parametrize(
    "args",
    [[], ["--host", "unreachable.invalid"], ["--url", "http://127.0.0.1:1/api/health"]],
)
def test_watchdog_requires_both_destinations(
    watchdog: Watchdog, args: list[str]
) -> None:
    state = watchdog.root / "state"
    result = subprocess.run(
        [str(SCRIPT), "--state-file", str(state / "watchdog"), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "--host" in result.stderr and "--url" in result.stderr
    assert not state.exists()


@pytest.mark.parametrize(
    ("ssh_status", "retry", "expected"),
    [
        (255, "down", "ssh unreachable"),
        (0, "down", "still down"),
        (0, "up", "restarted OK"),
        (255, "up", "restarted OK"),
    ],
)
def test_776_watchdog_alerts_match_health_after_restart(
    watchdog: Watchdog, ssh_status: int, retry: str, expected: str
) -> None:
    assert watchdog.run(now=100, retry=retry, ssh=ssh_status) == (
        0 if retry == "up" else 1
    )
    (message,) = watchdog.messages()
    assert "unreachable.invalid" in message
    assert expected in message
    if retry == "down":
        assert "restarted OK" not in message


def test_1451_watchdog_deduplicates_failures_but_reports_a_new_failure_kind(
    watchdog: Watchdog,
) -> None:
    assert watchdog.run(now=100, first="up") == 0
    assert not watchdog.messages()
    assert watchdog.run(now=400, ssh=255) == 1
    assert watchdog.run(now=700, ssh=255) == 1
    (outage,) = watchdog.messages()
    assert "ssh unreachable" in outage

    assert watchdog.run(now=1000) == 1
    assert watchdog.run(now=1300) == 1
    _, changed = watchdog.messages()
    assert "still down after restart" in changed


@pytest.mark.parametrize("first", ["up", "down"])
def test_1451_watchdog_reports_recovery_once_and_alerts_on_next_outage(
    watchdog: Watchdog, first: str
) -> None:
    assert watchdog.run(now=100) == 1
    assert watchdog.run(now=400, first=first, retry="up") == 0
    assert watchdog.run(now=700, first=first, retry="up") == 0
    _, recovery = watchdog.messages()
    assert "recovered" in recovery

    assert watchdog.run(now=1000) == 1
    _, _, new_outage = watchdog.messages()
    assert "down" in new_outage


def test_1451_watchdog_reminds_only_after_interval_since_last_alert(
    watchdog: Watchdog,
) -> None:
    assert watchdog.run(now=100) == 1
    assert watchdog.run(now=3699) == 1
    (outage,) = watchdog.messages()
    assert "reminder" not in outage

    assert watchdog.run(now=3700) == 1
    assert watchdog.run(now=4000) == 1
    _, reminder = watchdog.messages()
    assert "reminder" in reminder

    assert watchdog.run(now=7300) == 1
    _, _, next_reminder = watchdog.messages()
    assert "reminder" in next_reminder


def test_1451_watchdog_reports_first_successful_restart_after_a_healthy_check(
    watchdog: Watchdog,
) -> None:
    assert watchdog.run(now=100, first="up") == 0
    assert watchdog.run(now=400, retry="up") == 0
    assert watchdog.run(now=700, retry="up") == 0
    (recovery,) = watchdog.messages()
    assert "restarted OK" in recovery

    assert watchdog.run(now=1000, first="up") == 0
    assert watchdog.run(now=1300, retry="up") == 0
    _, next_recovery = watchdog.messages()
    assert "restarted OK" in next_recovery


@pytest.mark.parametrize("old_flock", [False, True])
def test_1451_overlapping_watchdogs_send_only_one_outage_alert(
    watchdog: Watchdog, old_flock: bool
) -> None:
    # A second check starts while the first is waiting for its SSH restart.
    root = shlex.quote(str(watchdog.root))
    nested = shlex.join(watchdog.command(100))
    if old_flock:
        flock = watchdog.root / "bin" / "flock"
        flock.write_text("#!/bin/sh\nexit 2\n")
        flock.chmod(0o755)
    (watchdog.root / "bin" / "ssh").write_text(
        "#!/bin/sh\n"
        f"if [ ! -f {root}/overlap ]; then\n"
        f"    touch {root}/overlap\n"
        f"    {nested}\n"
        "fi\n"
        "exit 0\n"
    )
    assert watchdog.run(now=100) == 1
    (outage,) = watchdog.messages()
    assert "down" in outage
