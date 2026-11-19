"""Desktop qualification planning and durable evidence without genome downloads."""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify_30x.ps1"
HELPERS = ROOT / "scripts" / "qualify_30x_helpers.ps1"
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 required")


@pytest.mark.parametrize("phase", ["all", "prepare", "slice", "full"])
def test_1024_desktop_dry_run_never_creates_workspace(
    tmp_path: Path, phase: str
) -> None:
    workspace = tmp_path / "space & apostrophe's"
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-Root",
            str(workspace),
            "-Phase",
            phase,
            "-Threads",
            "2",
            "-DryRun",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert not workspace.exists()
    assert "No sample data is uploaded" in result.stdout
    expected = {
        "prepare": "download_bam",
        "slice": "forced_missing_input",
        "full": "full_realign",
    }
    for selected, step in expected.items():
        assert (step in result.stdout) == (phase in ("all", selected))


def test_1024_desktop_evidence_survives_failure_and_resumes(tmp_path: Path) -> None:
    runner = ROOT / "tests" / "powershell" / "qualify_30x.ps1"
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-File",
            str(runner),
            "-Helpers",
            str(HELPERS),
            "-Root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
    steps = {step["step"]: step for step in summary["steps"]}
    assert steps["success"]["status"] == "complete"
    assert steps["failure"]["status"] == "failed"
    assert steps["failure"]["exit_code"] == 7
    assert steps["forced_missing_input"]["status"] == "complete"
    assert steps["forced_missing_input"]["exit_code"] == 3
    assert steps["vcf_stats"]["output_records"] == 42
    assert steps["bam_records"]["output_records"] == 21
    assert summary["machine"]["cpu"] == "OS boundary fixture"
    assert (tmp_path / "evidence.zip").is_file()
    with zipfile.ZipFile(tmp_path / "evidence.zip") as archive:
        assert any(name.endswith("summary.md") for name in archive.namelist())
        assert not any(name.endswith(".bam") for name in archive.namelist())
