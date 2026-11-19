"""#1138: setup reused a Pixi older than the CLI release's requires-pixi."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "examples" / "WGSExtract" / "scripts"
CLI_ROOT = REPO_ROOT.parent


def minimum_from_pixi_toml() -> str:
    for line in (CLI_ROOT / "pixi.toml").read_text().splitlines():
        if line.startswith("requires-pixi"):
            return line.split('">=', 1)[1].rstrip('"')
    raise AssertionError("pixi.toml declares no requires-pixi")


@unittest.skipIf(sys.platform == "win32", "POSIX setup scripts")
class PreinstalledPixiTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="wgsextract-pixi-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def env(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("WGSEXTRACT_", "PIXI"))
        }
        return {**env, "HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin"}

    def fake_pixi(self, version: str, directory: Path | None = None) -> Path:
        directory = directory or self.bin
        directory.mkdir(parents=True, exist_ok=True)
        pixi = directory / "pixi"
        pixi.write_text(
            f'#!/bin/sh\n[ "$1" = --version ] && echo "pixi {version}"\nexit 0\n'
        )
        pixi.chmod(0o755)
        return pixi

    def check(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(SCRIPTS / "posix" / "check-preinstalled-pixi.sh")],
            env=self.env(),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_bundled_minimum_matches_cli_requires_pixi(self) -> None:
        bundled = (SCRIPTS / "wgsextract-pixi-minimum.txt").read_text().strip()
        self.assertEqual(bundled, minimum_from_pixi_toml())

    def test_issue_1138_check_does_not_accept_old_pixi_in_home(self) -> None:
        self.fake_pixi("0.69.0", self.home / ".pixi" / "bin")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0.69.0", result.stdout)
        self.assertIn(f">={minimum_from_pixi_toml()}", result.stdout)
        self.assertIn("install its own Pixi", result.stdout)
        self.assertNotIn("will reuse", result.stdout)

    def test_check_reuses_new_enough_pixi_on_path(self) -> None:
        pixi = self.fake_pixi(minimum_from_pixi_toml())
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"will reuse the Pixi {minimum_from_pixi_toml()}", result.stdout)
        self.assertIn(str(pixi), result.stdout)

    def test_issue_1138_check_rejects_old_explicit_pixi(self) -> None:
        pixi = self.fake_pixi("0.69.0", self.root / "explicit")
        env = {**self.env(), "PIXI": str(pixi)}
        result = subprocess.run(
            ["sh", str(SCRIPTS / "posix" / "check-preinstalled-pixi.sh")],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("0.69.0", result.stderr)
        self.assertIn("pixi self-update", result.stderr)

    def setup_pixi_home(self, required: str) -> str:
        """The PIXI_HOME setup hands the release's install.sh."""
        source = self.root / "source" / "wgsextract-cli"
        source.mkdir(parents=True)
        (source / "pixi.toml").write_text(
            f'[workspace]\nrequires-pixi = ">={required}"\n'
        )
        report = self.root / "pixi-home"
        # The release's installer is the boundary: it reports what setup gave it.
        (source / "install.sh").write_text(
            f"#!/bin/sh\nprintf '%s' \"${{PIXI_HOME:-}}\" > '{report}'\n"
        )
        archive = self.root / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(source, arcname="wgsextract-cli")
        install_dir = self.root / "runtime"
        env = {
            **self.env(),
            "WGSEXTRACT_ARCHIVE_URL": str(archive),
            "WGSEXTRACT_INSTALL_DIR": str(install_dir),
        }
        result = subprocess.run(
            ["sh", str(SCRIPTS / "posix" / "setup-wgsextract-pixi.sh")],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return report.read_text()

    @unittest.skipIf(shutil.which("tar") is None, "needs tar")
    def test_issue_1138_old_pixi_keeps_embedded_pixi_state_in_runtime(self) -> None:
        self.fake_pixi("0.69.0", self.home / ".pixi" / "bin")
        pixi_home = self.setup_pixi_home("0.82.0")
        self.assertEqual(pixi_home, str(self.root / "runtime" / ".pixi"))

    @unittest.skipIf(shutil.which("tar") is None, "needs tar")
    def test_new_enough_user_pixi_keeps_its_own_home(self) -> None:
        self.fake_pixi("0.82.0", self.home / ".pixi" / "bin")
        self.assertEqual(self.setup_pixi_home("0.82.0"), "")


if __name__ == "__main__":
    unittest.main()
