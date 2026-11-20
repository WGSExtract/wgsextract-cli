"""Exercise source ZIP restoration without the host's Archive module."""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELLS = [
    shell
    for name in ("powershell", "pwsh")
    if (shell := shutil.which(name)) is not None
]


@pytest.mark.parametrize("shell", SHELLS or [None])
@pytest.mark.parametrize("installer", ["gui", "standalone"])
def test_windows_archive_module_mismatch_does_not_break_install(
    tmp_path: Path, shell: str | None, installer: str
) -> None:
    if shell is None:
        pytest.skip("PowerShell is not installed")
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("source/install_windows.bat", "installer")
        bundle.writestr("source/pixi.toml", "manifest")
        bundle.writestr("source/folder with spaces/data.txt", "payload")
    app = tmp_path / "installed app"
    if installer == "gui":
        source = (
            ROOT / "gui/examples/WGSExtract/scripts/windows/setup-wgsextract-pixi.ps1"
        ).read_text()
        functions = source.split("function Remove-DirectoryWithRetry", 1)[1]
        functions = functions.split("# Pre-state snapshot", 1)[0]
        command = (
            "function Remove-DirectoryWithRetry"
            + functions
            + "\nRestore-AppSource -Archive $env:TEST_ARCHIVE -AppDir $env:TEST_APP"
        )
    else:
        source = (ROOT / "install_windows.bat").read_text()
        command = next(
            line.split(' -Command "', 1)[1].removesuffix('"')
            for line in source.splitlines()
            if "Bootstrapped WGS Extract CLI into" in line
        )
        app.mkdir()
        (app / "install_windows.bat").write_text("original bootstrap")
    script = tmp_path / "restore.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "function Expand-Archive { throw 'incompatible Archive module' }\n"
        + command
        + "\n"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env={
            **os.environ,
            "TEST_ARCHIVE": str(archive),
            "TEST_APP": str(app),
            "WGSE_BOOTSTRAP_ARCHIVE_URL": str(archive),
            "WGSE_BOOTSTRAP_DIR": str(app),
            "WGSE_BOOTSTRAP_TMP": str(tmp_path / "bootstrap"),
            "WGSE_REPO_URL": "https://github.com/WGSExtract/wgsextract-cli",
            "WGSE_REQUESTED_REF": "unused",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (app / "pixi.toml").read_text() == "manifest"
    assert (app / "folder with spaces/data.txt").read_text() == "payload"
    assert (app / "install_windows.bat").read_text() == (
        "installer" if installer == "gui" else "original bootstrap"
    )
