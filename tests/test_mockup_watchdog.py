"""Issue #776: watchdog alerts describe actual recovery, including SSH failure."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "gui/mockups/hosting/watchdog.sh"
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="The systemd watchdog runs on POSIX hosts"
)


@pytest.mark.parametrize(
    ("ssh_status", "recovered", "expected"),
    [
        (255, False, "ssh unreachable"),
        (0, False, "still down"),
        (0, True, "restarted OK"),
    ],
)
def test_776_watchdog_alerts_after_health_check_even_when_ssh_fails(
    tmp_path: Path,
    ssh_status: int,
    recovered: bool,
    expected: str,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = shlex.quote(str(tmp_path / "health-probes"))
    rechecked = shlex.quote(str(tmp_path / "post-restart-probe"))
    alerts = tmp_path / "alerts"
    commands = {
        "ssh": f"exit {ssh_status}\n",
        "curl": (
            f"if [ -f {calls} ]; then\n"
            + f"touch {rechecked}\n"
            + ("echo '{\"ok\": true}'; exit 0\n" if recovered else "exit 22\n")
            + f"fi\ntouch {calls}\nexit 22\n"
        ),
        "coordinator": (
            f"test -f {rechecked}\n"
            f"printf '%s\\n' \"$4\" >> {shlex.quote(str(alerts))}\n"
        ),
    }
    for name, body in commands.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\nset -eu\n" + body)
        path.chmod(0o755)
    result = subprocess.run(
        [
            str(SCRIPT),
            "--host",
            "unreachable.invalid",
            "--retry-delay",
            "0",
            "--agent-coord",
            str(bin_dir / "coordinator"),
        ],
        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.defpath},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (0 if recovered else 1)
    message = alerts.read_text()
    assert "unreachable.invalid" in message
    assert expected in message
    if not recovered:
        assert "restarted OK" not in message
