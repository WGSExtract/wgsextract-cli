"""The Linux app scenario's `sudo apt-get` (guest/user-apt.sh): which failures
it reports as a package-mirror outage, and that apt's status comes through."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
USER_APT = ROOT / "scripts/installlab/guest/user-apt.sh"
ANNOTATION = "::error title=Upstream package mirror unreachable::"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX guest script")

# sudo drops the VAR=value words it was given and runs the rest; apt-get prints
# FAKE_APT_OUTPUT and exits FAKE_APT_RC.
FAKE_SUDO = """#!/bin/sh
while [ $# -gt 0 ]; do
    case $1 in *=*) shift ;; *) break ;; esac
done
exec "$@"
"""
FAKE_APT_GET = """#!/bin/sh
printf '%s\\n' "$FAKE_APT_OUTPUT"
exit "$FAKE_APT_RC"
"""
DRIVER = """
. "$USER_APT"
user_apt install -y -q /tmp/app.deb
echo "user_apt rc=$?"
report_apt_outage
"""


def run_user_apt(
    tmp_path: Path, output: str, rc: int
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("sudo", FAKE_SUDO), ("apt-get", FAKE_APT_GET)):
        (bin_dir / name).write_text(text, newline="\n")
        (bin_dir / name).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "USER_APT": str(USER_APT),
        "APT_OUTAGE": str(tmp_path / "apt-outage.txt"),
        "FAKE_APT_OUTPUT": output,
        "FAKE_APT_RC": str(rc),
    }
    return subprocess.run(
        ["sh", "-c", DRIVER], env=env, capture_output=True, text=True, timeout=60
    )


@pytest.mark.parametrize(
    ("output", "rc", "outage"),
    [
        ("Setting up wgs-extract (1.0) ...", 0, False),
        # The 2026-11-11 outage: names that don't resolve.
        (
            "E: Failed to fetch http://archive.ubuntu.com/ubuntu/pool/main/x.deb  "
            "Temporary failure resolving 'archive.ubuntu.com'",
            100,
            True,
        ),
        (
            "E: Failed to fetch http://mirrors.edge.kernel.org/x.deb  Connection failed",
            100,
            True,
        ),
        # The time limit alone may be a hung maintainer script: the package's fault.
        ("Setting up wgs-extract (1.0) ...", 124, False),
        ("Setting up wgs-extract (1.0) ...", 137, False),
        # A bad signature fails as itself, even alongside a fetch error.
        (
            "W: GPG error: http://archive.ubuntu.com noble InRelease: NO_PUBKEY 871920D1991BC93C\n"
            "E: Failed to fetch http://archive.ubuntu.com/ubuntu/dists/noble/InRelease",
            100,
            False,
        ),
        ("dpkg: error processing package wgs-extract (--configure)", 1, False),
    ],
)
def test_user_apt_reports_only_network_failures_as_outages(
    tmp_path: Path, output: str, rc: int, outage: bool
) -> None:
    run = run_user_apt(tmp_path, output, rc)
    assert run.returncode == 0, run.stderr
    assert f"user_apt rc={rc}\n" in run.stdout
    # apt's own output reaches the step log either way.
    assert output.splitlines()[0] in run.stdout
    assert (ANNOTATION in run.stdout) is outage
    if outage:
        assert "apt-get install could not reach the Ubuntu mirrors" in run.stdout
    # Reported once: the next step starts with no outage on record.
    assert not (tmp_path / "apt-outage.txt").exists()
