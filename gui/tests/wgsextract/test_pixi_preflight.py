"""Issue #1138: setup must reject incompatible existing Pixi without updating it."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

GUI = Path(__file__).resolve().parents[2]
ROOT = GUI.parent
SCRIPTS = GUI / "examples/WGSExtract/scripts"


class PixiPreflightTests(unittest.TestCase):
    def test_1138_bundle_requirement_is_generated_from_workspace(self) -> None:
        requirement = tomllib.loads((ROOT / "pixi.toml").read_text())["workspace"][
            "requires-pixi"
        ]
        self.assertEqual(
            ">=" + (SCRIPTS / "pixi-minimum-version.txt").read_text().strip(),
            requirement,
        )

    @unittest.skipIf(os.name == "nt", "POSIX setup script")
    def test_1138_posix_preflight_old_current_newer_missing_and_broken(self) -> None:
        for version in ("0.69.0", "0.82.0", "0.100.0", None, "invalid", "0.82.0-rc.1"):
            for location in ("home", "path", "explicit"):
                with (
                    self.subTest(version=version, location=location),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    home = Path(directory)
                    binary = home / ".pixi/bin/pixi"
                    binary.parent.mkdir(parents=True)
                    if version is not None:
                        binary.write_text(f"#!/bin/sh\nprintf 'pixi {version}\\n'\n")
                        binary.chmod(0o755)
                    env = {
                        **os.environ,
                        "HOME": str(home),
                        "PATH": os.defpath,
                        "PIXI": "",
                    }
                    if location == "path":
                        env["PATH"] = str(binary.parent) + os.pathsep + os.defpath
                    elif location == "explicit" and version is not None:
                        env["PIXI"] = str(binary)
                    result = subprocess.run(
                        ["sh", str(SCRIPTS / "posix/check-preinstalled-pixi.sh")],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    self.assertEqual(
                        result.returncode == 0,
                        version in (None, "0.82.0", "0.100.0"),
                        result.stderr,
                    )
                    if version is None:
                        self.assertIn("setup will install", result.stdout)
                        self.assertFalse(binary.exists())
                    else:
                        self.assertIn(f"pixi {version}", binary.read_text())
                        if result.returncode:
                            self.assertIn(">=0.82.0", result.stderr)
                            self.assertIn("self-update", result.stderr)
                            self.assertIn(str(binary), result.stderr)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_1138_windows_preflight_and_installer_reject_old_pixi(self) -> None:
        for version in ("0.69.0", "0.82.0", "0.100.0", "invalid", "0.82.0-rc.1"):
            for script in (
                SCRIPTS / "windows/check-preinstalled-pixi.ps1",
                ROOT / "scripts/bootstrap_windows_prereqs.ps1",
            ):
                with (
                    self.subTest(version=version, script=script.name),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    home = Path(directory)
                    binary = home / "pixi"
                    binary.write_text(f"#!/bin/sh\nprintf 'pixi {version}\\n'\n")
                    binary.chmod(0o755)
                    env = {
                        **os.environ,
                        "HOME": str(home),
                        "USERPROFILE": str(home),
                        "LOCALAPPDATA": str(home),
                        "PIXI_HOME": "",
                        "PIXI": "",
                        "WGSEXTRACT_MSYS2_ROOT": str(home / "msys64"),
                        "PATH": str(home) + os.pathsep + os.defpath,
                    }
                    result = subprocess.run(
                        [str(shutil.which("pwsh")), "-NoProfile", "-File", str(script)],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )
                    self.assertEqual(
                        result.returncode == 0,
                        version in ("0.82.0", "0.100.0"),
                        result.stderr,
                    )
                    self.assertIn(f"pixi {version}", binary.read_text())
                    if result.returncode:
                        self.assertIn(">=0.82.0", result.stderr)
                        self.assertIn("self-update", result.stderr)


if __name__ == "__main__":
    unittest.main()
