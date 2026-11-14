"""Regression tests for #766: Pixi drift and indexed benchmark folders."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_766_pixi_minimum_matches_ci_pin() -> None:
    manifest = tomllib.loads((ROOT / "pixi.toml").read_text())
    action = (ROOT / ".github/actions/setup-pixi/action.yml").read_text()
    pins = re.findall(r"pixi-version:\s*v(\d+\.\d+\.\d+)", action)
    assert pins
    assert manifest["workspace"]["requires-pixi"] == f">={pins[0]}"
    assert len(set(pins)) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX setup")
@pytest.mark.parametrize(
    ("version", "accepted"),
    [
        ("0.70.2", False),
        ("0.81.99", False),
        ("0.82.0", True),
        ("0.100.0", True),
        ("1.0.0", True),
    ],
)
def test_766_setup_rejects_old_pixi_before_manifest_loading(
    tmp_path: Path, version: str, accepted: bool
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/check_pixi_version.sh", scripts)
    shutil.copy(ROOT / "pixi.toml", tmp_path)
    binary = tmp_path / "pixi"
    binary.write_text(f"#!/bin/sh\nprintf 'pixi {version}\\n'\n")
    binary.chmod(0o755)
    result = subprocess.run(
        ["sh", str(scripts / "check_pixi_version.sh")],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted
    if not accepted:
        assert version in result.stderr
        assert "pixi self-update" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX setup")
@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_766_setup_excludes_macos_heavy_folders_idempotently(
    tmp_path: Path, platform: str
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for script in ("dev_setup.sh", "check_pixi_version.sh"):
        shutil.copy(ROOT / "scripts" / script, scripts)
    shutil.copy(ROOT / "pixi.toml", tmp_path)
    home = tmp_path / "home"
    binaries = home / ".pixi/bin"
    binaries.mkdir(parents=True)
    for name, body in {
        "uname": f"printf '{platform}\\n'",
        "xcode-select": "exit 0",
        "pixi": 'if [ "$1" = "--version" ]; then echo "pixi 0.82.0"; fi',
        "git": 'echo "test identity"',
        "just": 'echo "just 1.0.0"',
        "gh": "exit 0",
        "copilot": "exit 0",
    }.items():
        binary = binaries / name
        binary.write_text(f"#!/bin/sh\n{body}\n")
        binary.chmod(0o755)
    for _ in range(2):
        result = subprocess.run(
            ["sh", str(scripts / "dev_setup.sh"), "--skip-tests"],
            env={
                **os.environ,
                "HOME": str(home),
                "PATH": f"{binaries}:{os.environ['PATH']}",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Setup complete" in result.stdout
        for folder in ("out", "scratch", "tmp"):
            assert (tmp_path / folder / ".metadata_never_index").is_file() is (
                platform == "Darwin"
            )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is not installed")
@pytest.mark.parametrize(
    ("version", "accepted"), [("0.70.2", False), ("0.82.0", True), ("0.100.0", True)]
)
def test_766_windows_setup_checks_pixi_before_installing(
    tmp_path: Path, version: str, accepted: bool
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/dev_setup.ps1", scripts)
    shutil.copy(ROOT / "pixi.toml", tmp_path)
    (scripts / "bootstrap_windows_prereqs.ps1").write_text(
        "# Installer boundary: already present.\n"
    )
    runner = tmp_path / "run.ps1"
    runner.write_text(
        f"""
function global:pixi {{
    $global:LASTEXITCODE = 0
    if ($args[0] -eq "--version") {{ "pixi {version}" }}
    elseif ($args[0] -eq "install") {{ "Environment installation reached" }}
}}
function global:git {{ $global:LASTEXITCODE = 0; "test identity" }}
function global:just {{ $global:LASTEXITCODE = 0; "just 1.0.0" }}
function global:gh {{ $global:LASTEXITCODE = 0 }}
function global:copilot {{ $global:LASTEXITCODE = 0 }}
$env:USERPROFILE = $PSScriptRoot
& (Join-Path $PSScriptRoot "scripts/dev_setup.ps1") -SkipTests
"""
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(runner)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted, result.stderr
    if accepted:
        assert "Setup complete" in result.stdout
    else:
        assert version in result.stderr
        assert "pixi self-update" in result.stderr
        assert "Environment installation reached" not in result.stdout
