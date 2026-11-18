import os
import shutil
import subprocess
from pathlib import Path

import pytest

GUEST = Path(__file__).resolve().parents[1] / "scripts" / "installlab" / "guest"


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
@pytest.mark.parametrize(
    ("workspace", "folder"),
    [
        ("bundle", "genomes"),
        ("bundle", "datasets"),
        ("bundle", "output"),
        ("users", "mygenomes"),
        ("users", "output"),
    ],
)
def test_801_gui_collects_info_from_released_and_tree_plan_locations(
    tmp_path: Path, platform: str, workspace: str, folder: str
) -> None:
    shell = shutil.which("pwsh" if platform == "windows" else "sh")
    if shell is None:
        pytest.skip(f"{platform} shell not installed")
    bundle = tmp_path / "bundle workspace"
    users = tmp_path / "users workspace"
    results = tmp_path / "results"
    (results / "workload").mkdir(parents=True)
    origin = (bundle if workspace == "bundle" else users) / folder / "sample"
    origin.mkdir(parents=True)
    info = origin / "HG00096.hg19-mini.bam.wgse_info.json"
    info.write_text('{"sample": "HG00096", "coverage": 30}')
    if platform == "windows":
        text = (GUEST / "windows" / "gui-scenario.ps1").read_text()
        start = 'if (Test-Path -LiteralPath (Join-Path $Workspace "lab-output")) {'
        collector = (
            start + text.split(start, 1)[1].split('Save-Snapshot "after_install"', 1)[0]
        )
        script = tmp_path / "collect.ps1"
        script.write_text(
            "$Workspace, $UsersWorkspace, $R = $args\n" + collector,
            encoding="utf-8",
        )
        command = [
            shell,
            "-NoProfile",
            "-File",
            str(script),
            str(bundle),
            str(users),
            str(results),
        ]
        env = os.environ.copy()
    else:
        text = (GUEST / f"gui-{platform}.sh").read_text()
        start = '    if [ -d "$WORKSPACE/lab-output" ]; then'
        collector = start + text.split(start, 1)[1].split("    return $rc", 1)[0]
        command = [shell, "-c", collector]
        env = {
            **os.environ,
            "WORKSPACE": str(bundle),
            "USERS_WORKSPACE": str(users),
            "R": str(results),
        }
    # Missing optional search roots need not make find succeed; the copied
    # metrics are the evidence that the judge requires.
    subprocess.run(command, env=env, capture_output=True, check=False)
    collected = results / "workload" / "info" / info.name
    assert collected.read_text() == info.read_text()
