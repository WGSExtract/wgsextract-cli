import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")


def powershell(
    script: Path, *args: str, home: Path
) -> subprocess.CompletedProcess[str]:
    home.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-File", str(script), *args],
        env={
            **os.environ,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home / "Local"),
            "APPDATA": str(home / "Roaming"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("remove", [False, True])
@pytest.mark.parametrize("data", [False, True])
def test_1087_windows_full_removal_cleans_owned_config_but_preserves_data(
    tmp_path: Path, remove: bool, data: bool
) -> None:
    home = tmp_path / "lab user"
    workspace = home / "wgs-extract"
    workspace.mkdir(parents=True)
    config = workspace / "config.toml"
    config.write_text("cpu_threads = 2\n")
    marker = workspace / ".users-workspace"
    marker.write_text("WGS Extract users workspace\n")
    sample = workspace / "mygenomes" / "sample.bam"
    if data:
        sample.parent.mkdir()
        sample.write_bytes(b"keep")

    result = powershell(
        ROOT / "scripts/remove_users_workspace_config.ps1",
        "-Workspace",
        str(workspace),
        *(["-RemoveConfig"] if remove else []),
        home=home,
    )

    assert result.returncode == 0, result.stderr
    assert config.exists() is not remove
    if data:
        assert sample.read_bytes() == b"keep"
        assert marker.is_file()
    elif remove:
        assert not workspace.exists()
    else:
        assert marker.is_file()


@pytest.mark.parametrize(
    "marker", [None, "", "Not a WGS workspace\n", "wgs extract users workspace\n"]
)
def test_1087_windows_uninstall_keeps_unmarked_workspace_config(
    tmp_path: Path, marker: str | None
) -> None:
    workspace = tmp_path / "unmarked"
    workspace.mkdir()
    config = workspace / "config.toml"
    config.write_text("unrelated = true\n")
    if marker is not None:
        (workspace / ".users-workspace").write_text(marker)
    result = powershell(
        ROOT / "scripts/remove_users_workspace_config.ps1",
        "-Workspace",
        str(workspace),
        "-RemoveConfig",
        home=tmp_path / "home",
    )
    assert result.returncode == 0, result.stderr
    assert config.read_text() == "unrelated = true\n"


def test_1087_windows_uninstall_refuses_a_workspace_link(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    config = shared / "config.toml"
    config.write_text("cpu_threads = 3\n")
    (shared / ".users-workspace").write_text("WGS Extract users workspace\n")
    link = tmp_path / "linked"
    try:
        link.symlink_to(shared, target_is_directory=True)
    except OSError:
        pytest.skip("directory links unavailable")
    result = powershell(
        ROOT / "scripts/remove_users_workspace_config.ps1",
        "-Workspace",
        str(link),
        "-RemoveConfig",
        home=tmp_path / "home",
    )
    assert result.returncode != 0
    assert config.read_text() == "cpu_threads = 3\n"


@pytest.mark.skipif(sys.platform != "win32", reason="requires native cmd.exe")
def test_1087_windows_batch_full_removal_reaches_workspace_cleanup(
    tmp_path: Path,
) -> None:
    install = tmp_path / "install"
    (install / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "uninstall_windows.bat", install)
    shutil.copy2(
        ROOT / "scripts/remove_users_workspace_config.ps1", install / "scripts"
    )
    home = tmp_path / "lab user"
    workspace = home / "wgs-extract"
    workspace.mkdir(parents=True)
    (workspace / "config.toml").write_text("cpu_threads = 2\n")
    (workspace / ".users-workspace").write_text("WGS Extract users workspace\n")
    cache = install / "tmp" / "cache"
    help_cache = cache / "cli" / "version-registry"
    help_cache.mkdir(parents=True)
    (help_cache / "help-index.json").write_text("{}", encoding="utf-8")
    marker = install / "tmp" / ".managed-help-cache"
    marker.touch()
    unrelated = cache / "other"
    unrelated.touch()
    result = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            str(install / "uninstall_windows.bat"),
            "--yes",
            "--remove-config",
        ],
        env={
            **os.environ,
            "USERPROFILE": str(home),
            "HOME": str(home),
            "LOCALAPPDATA": str(home / "Local"),
            "APPDATA": str(home / "Roaming"),
            "PIXI_HOME": str(home / ".pixi"),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not workspace.exists()
    assert not marker.exists()
    assert not help_cache.exists()
    assert unrelated.exists()


def test_1087_windows_uninstall_answers_new_workspace_prompt_without_accepting_errors(
    tmp_path: Path,
) -> None:
    helper = ROOT / "scripts/installlab/guest/windows/uninstall-confirmation.ps1"
    runner = tmp_path / "questions.ps1"
    runner.write_text(
        "param([string]$Helper)\n. $Helper\n"
        "$new = \"Yes | No | Also delete WGSExtract's users workspace?`n"
        'C:\\Users\\lab user\\wgs-extract"\n'
        '$old = "Also delete WGSExtract\'s workspace folder?"\n'
        "[ordered]@{\n"
        " full = (Get-WorkspaceRemovalAnswer $new $true)\n"
        " keep = (Get-WorkspaceRemovalAnswer $new $false)\n"
        " legacy = (Get-WorkspaceRemovalAnswer $old $true)\n"
        " error = (Get-WorkspaceRemovalAnswer 'Some setup files could not be removed.' $true)\n"
        "} | ConvertTo-Json -Compress\n"
    )
    result = powershell(runner, str(helper), home=tmp_path / "home")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "full": "Yes",
        "keep": "No",
        "legacy": "Yes",
        "error": None,
    }
