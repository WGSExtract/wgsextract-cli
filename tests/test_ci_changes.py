"""Regression coverage for area routing and small-PR landing."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_changes import routes
from scripts.pr_checks_ready import checks_ready


@pytest.mark.parametrize(
    ("paths", "enabled"),
    [
        (
            ["AGENTS.md", "native/README.md", ".github/copilot-instructions.md"],
            {"docs"},
        ),
        (["docs/api.md", "docs/commands.md"], {"docs"}),
        (["native/bwa/bwa.c"], {"native"}),
        (["gui/mockups/index.html", "site_src/index.md"], {"site"}),
        (["gui/scripts/site_layout.py"], {"site"}),
        (["gui/app/shared/config.ts"], set()),
        (["src/wgsextract_cli/commands/info.py"], {"cli", "docs"}),
        (["pixi.lock"], {"cli", "docs", "install"}),
        (["scripts/installlab/lab.py"], {"cli", "docs", "install"}),
        (["install.sh"], {"cli", "docs", "install"}),
        (["uninstall.sh"], {"cli", "docs", "install"}),
        (["uninstall_windows.bat"], {"cli", "docs", "install"}),
        (["scripts/bootstrap_windows_prereqs.ps1"], {"cli", "docs", "install"}),
        (["src/wgsextract_cli/main.py"], {"cli", "docs", "install"}),
        (["gui/app/web/packagers/tauri/Cargo.lock"], {"install"}),
        (
            ["native/bwa/bwa.c", "src/wgsextract_cli/main.py"],
            {"native", "cli", "docs", "install"},
        ),
        (["unknown/new-config"], {"cli", "docs"}),
    ],
)
def test_757_routes_isolated_and_mixed_prs(paths: list[str], enabled: set[str]) -> None:
    assert {key for key, value in routes(paths).items() if value} == enabled


def test_757_label_forces_install_lab_on_a_docs_pr() -> None:
    assert routes(["README.md"], force_install_lab=True)["install"]


def test_757_install_lab_label_does_not_repeat_the_cli_matrix() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "ci_changes.py"
    output = subprocess.check_output(
        [
            sys.executable,
            str(script),
            "--base",
            "HEAD",
            "--head",
            "HEAD",
            "--label-event",
            "--force-install-lab",
        ],
        text=True,
    )
    assert dict(line.split("=") for line in output.splitlines()) == {
        "cli": "false",
        "docs": "false",
        "native": "false",
        "site": "false",
        "install": "true",
    }


def test_757_diff_routes_both_sides_of_a_cross_area_rename(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], text=True
        ).strip()

    git("init", "--quiet")
    git("config", "user.name", "CI routing test")
    git("config", "user.email", "ci@example.invalid")
    (tmp_path / "source.py").write_text("print('hello')\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "native").mkdir()
    git("mv", "source.py", "native/source.py")
    git("commit", "--quiet", "-m", "move")
    script = Path(__file__).resolve().parents[1] / "scripts" / "ci_changes.py"
    output = subprocess.check_output(
        [sys.executable, str(script), "--base", base, "--head", "HEAD"],
        cwd=tmp_path,
        text=True,
    )
    assert "cli=true" in output
    assert "native=true" in output


@pytest.mark.parametrize(
    ("buckets", "ready"),
    [
        ([], False),
        (["skipping"], False),
        (["pass"], True),
        (["pass", "skipping"], True),
        (["pass", "pending"], False),
        (["pass", "fail"], False),
        (["pass", "cancel"], False),
        (["pass", "unknown"], False),
    ],
)
def test_757_landing_accepts_one_green_check_not_a_fixed_matrix_size(
    buckets: list[str], ready: bool
) -> None:
    assert checks_ready([{"bucket": bucket} for bucket in buckets]) is ready


def test_757_landing_cli_rejects_an_empty_check_report() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "pr_checks_ready.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps([]),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
