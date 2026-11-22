"""A real mounted volume without hard links, as on exFAT external drives."""

import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def exfat_volume(tmp_path: Path) -> Iterator[Path]:
    """The root of a small exFAT disk image attached for this test (macOS)."""
    hdiutil = shutil.which("hdiutil")
    if sys.platform != "darwin" or hdiutil is None:
        pytest.skip("mounting an exFAT image without root needs macOS hdiutil")
    image, mount = tmp_path / "exfat.dmg", tmp_path / "exfat"
    for command in (
        ["create", "-size", "8m", "-fs", "ExFAT", "-volname", "WGSE", str(image)],
        ["attach", "-nobrowse", "-mountpoint", str(mount), str(image)],
    ):
        done = subprocess.run(
            [hdiutil, *command, "-quiet"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if done.returncode:
            pytest.skip(f"hdiutil {command[0]} failed: {done.stderr.strip()}")
    try:
        yield mount
    finally:
        subprocess.run(
            [hdiutil, "detach", "-force", "-quiet", str(mount)],
            capture_output=True,
            timeout=120,
        )
