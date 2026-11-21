"""Exercise source ZIP restoration without the host's Archive module."""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_INSTALLER = ROOT / "install_windows.bat"
SHELLS = [
    shell
    for name in ("powershell", "pwsh")
    if (shell := shutil.which(name)) is not None
]


def _bootstrap_command() -> str:
    return next(
        line.split(' -Command "', 1)[1].removesuffix('"')
        for line in WINDOWS_INSTALLER.read_text().splitlines()
        if "Bootstrapped WGS Extract CLI into" in line
    )


def _bootstrap_env(app: Path, archive: Path, temp_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "TMPDIR": str(temp_dir),
        "WGSEXTRACT_ARCHIVE_URL": str(archive),
        "WGSE_BOOTSTRAP_ARCHIVE_URL": str(archive),
        "WGSE_BOOTSTRAP_DIR": str(app),
        "WGSE_BOOTSTRAP_TMP": str(app / "tmp/bootstrap"),
        "WGSE_REPO_URL": "https://github.com/WGSExtract/wgsextract-cli",
        "WGSE_REQUESTED_REF": "unused",
    }


@pytest.mark.parametrize("malformed", ["missing-manifest", "corrupt-zip"])
def test_1211_windows_bootstrap_rejects_bad_archives_without_touching_user_files(
    tmp_path: Path, malformed: str
) -> None:
    app = tmp_path / "installed app"
    previous_stage = app / "tmp/bootstrap"
    previous_stage.mkdir(parents=True)
    sentinel = previous_stage / "keep.txt"
    sentinel.write_text("user data")
    existing_file = app / "existing.txt"
    existing_file.write_text("previous file")
    temp_dir = tmp_path / "system temp"
    temp_dir.mkdir()
    archive = tmp_path / "source.zip"
    if malformed == "missing-manifest":
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("source/should-not-copy.txt", "partial update")
            bundle.writestr("source/existing.txt", "replacement")
            bundle.writestr("source/pixi.lock", "lock")
            bundle.writestr("source/scripts/bootstrap_windows_prereqs.ps1", "")
    else:
        archive.write_text("not a ZIP")

    if os.name == "nt":
        shutil.copyfile(WINDOWS_INSTALLER, app / WINDOWS_INSTALLER.name)
        command = [
            "cmd.exe",
            "/d",
            "/c",
            r".\install_windows.bat",
            "--allow-nonempty-bootstrap-dir",
        ]
    elif SHELLS:
        script = tmp_path / "bootstrap.ps1"
        script.write_text(_bootstrap_command() + "\n")
        command = [SHELLS[0], "-NoProfile", "-File", str(script)]
    else:
        pytest.skip("No Windows command interpreter or PowerShell available")
    result = subprocess.run(
        command,
        cwd=app,
        env={
            **_bootstrap_env(app, archive, temp_dir),
            "WGSE_ALLOW_NONEMPTY_BOOTSTRAP_DIR": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, result.stdout
    assert (
        "pixi.toml" if malformed == "missing-manifest" else "ExtractToDirectory"
    ) in result.stderr
    assert sentinel.read_text() == "user data"
    assert existing_file.read_text() == "previous file"
    assert not (app / "should-not-copy.txt").exists()
    assert not list(temp_dir.glob("wgse-bootstrap-*"))


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
        bundle.writestr("source/pixi.lock", "lock")
        bundle.writestr("source/scripts/bootstrap_windows_prereqs.ps1", "")
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
        command = _bootstrap_command()
        app.mkdir()
        (app / "install_windows.bat").write_text("original bootstrap")
    temp_dir = tmp_path / "system temp"
    temp_dir.mkdir()
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
            **_bootstrap_env(app, archive, temp_dir),
            "TEST_ARCHIVE": str(archive),
            "TEST_APP": str(app),
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
    assert not list(temp_dir.glob("wgse-bootstrap-*"))
