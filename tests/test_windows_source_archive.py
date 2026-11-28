"""Exercise source ZIP restoration without the host's Archive module."""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_INSTALLER = ROOT / "install_windows.bat"
GUI_INSTALLER = (
    ROOT / "gui/examples/WGSExtract/scripts/windows/setup-wgsextract-pixi.ps1"
)
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


@pytest.mark.parametrize(
    ("malformed", "reason"),
    [
        ("missing-manifest", "pixi.toml"),
        ("missing-package", "src/wgsextract_cli"),
        ("corrupt-zip", "ExtractToDirectory"),
    ],
)
def test_1211_windows_bootstrap_rejects_bad_archives_without_touching_user_files(
    tmp_path: Path, malformed: str, reason: str
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
    if malformed != "corrupt-zip":
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("source/should-not-copy.txt", "partial update")
            bundle.writestr("source/existing.txt", "replacement")
            bundle.writestr("source/pixi.lock", "lock")
            bundle.writestr("source/scripts/bootstrap_windows_prereqs.ps1", "")
            bundle.writestr("source/pyproject.toml", "package manifest")
            if malformed != "missing-manifest":
                bundle.writestr("source/pixi.toml", "manifest")
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
    assert reason in result.stderr
    assert sentinel.read_text() == "user data"
    assert existing_file.read_text() == "previous file"
    assert not (app / "should-not-copy.txt").exists()
    assert not list(temp_dir.glob("wgse-bootstrap-*"))


@pytest.mark.parametrize("shell", SHELLS or [None])
def test_1211_windows_cleanup_retries_locked_staging_after_an_archive_failure(
    tmp_path: Path, shell: str | None
) -> None:
    if shell is None:
        pytest.skip("PowerShell is not installed")
    app = tmp_path / "installed app"
    app.mkdir()
    temp_dir = tmp_path / "system temp"
    temp_dir.mkdir()
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("source/keep.txt", "must not install")
    script = tmp_path / "bootstrap.ps1"
    script.write_text(
        "$locked = $true\n"
        "function Remove-Item {\n"
        "    param([string]$LiteralPath, [switch]$Recurse, [switch]$Force)\n"
        "    if ($script:locked) { $script:locked = $false; throw 'Simulated file lock' }\n"
        "    Microsoft.PowerShell.Management\\Remove-Item @PSBoundParameters\n"
        "}\n" + _bootstrap_command() + "\n"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env=_bootstrap_env(app, archive, temp_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "pixi.toml" in result.stderr
    assert not list(app.iterdir())
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
        bundle.writestr("source/pyproject.toml", "package manifest")
        bundle.writestr("source/src/wgsextract_cli/__init__.py", "")
        bundle.writestr("source/folder with spaces/data.txt", "payload")
    app = tmp_path / "installed app"
    temp_dir = tmp_path / "system temp"
    temp_dir.mkdir()
    env = _bootstrap_env(app, archive, temp_dir)
    if installer == "gui":
        # Run the real Setup script and stop at the dependency-install boundary.
        install_dir = tmp_path / "runtime"
        app = install_dir / "app"
        pixi = tmp_path / "pixi.ps1"
        pixi.write_text("exit 1\n")
        env.update(
            TEST_GUI_INSTALLER=str(GUI_INSTALLER),
            WGSEXTRACT_INSTALL_DIR=str(install_dir),
            WGSEXTRACT_GUI_BUNDLE_WORKSPACE=str(tmp_path / "bundle"),
            WGSEXTRACT_MSYS2_ROOT=str(tmp_path / "no-msys2"),
            WGSEXTRACT_PIXI_BASE_DIR=str(tmp_path / "pixi-data"),
            LOCALAPPDATA=str(tmp_path / "local-data"),
            PIXI_HOME=str(tmp_path / "pixi-home"),
            PIXI=str(pixi),
        )
        command = (
            "function Invoke-FixtureInstaller { $global:LASTEXITCODE = 0 }\n"
            "$env:ComSpec = 'Invoke-FixtureInstaller'\n"
            "& $env:TEST_GUI_INSTALLER"
        )
    else:
        command = _bootstrap_command()
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
        env=env,
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
