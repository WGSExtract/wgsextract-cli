"""Regression coverage for area routing and small-PR landing."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_changes import routes
from scripts.ci_test_dependencies import DOCS_TEST_MODULES
from scripts.ci_test_dependencies import test_inputs as referenced_inputs
from scripts.pr_checks_ready import checks_ready


@pytest.mark.parametrize(
    ("paths", "enabled"),
    [
        (
            ["AGENTS.md", "native/README.md", ".github/copilot-instructions.md"],
            {"docs"},
        ),
        (["docs/api.md", "docs/commands.md"], {"docs"}),
        (["native/bwa/bwa.c"], {"native", "docs"}),
        # Mockup changes also feed the cross-platform review-server regressions.
        (["gui/mockups/index.html", "site_src/index.md"], {"site", "cli", "docs"}),
        (["gui/scripts/site_layout.py"], {"site", "cli", "docs"}),
        (["gui/app/shared/config.ts"], {"docs"}),
        (["src/wgsextract_cli/commands/info.py"], {"cli", "docs"}),
        (["pixi.lock"], {"cli", "docs", "install"}),
        (["scripts/installlab/lab.py"], {"cli", "docs", "install"}),
        ([".github/actions/download-cache/action.yml"], {"cli", "docs", "install"}),
        (["install.sh"], {"cli", "docs", "install"}),
        (["uninstall.sh"], {"cli", "docs", "install"}),
        (["uninstall_windows.bat"], {"cli", "docs", "install"}),
        (["scripts/bootstrap_windows_prereqs.ps1"], {"cli", "docs", "install"}),
        (["src/wgsextract_cli/main.py"], {"cli", "docs", "install"}),
        (["gui/app/web/packagers/tauri/Cargo.lock"], {"install", "docs"}),
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
        "python_files": "[]",
    }


@pytest.mark.parametrize(
    "path",
    [
        "site_src/cli.md",
        "site_src/index.md",
        "site_src/install.md",
        "gui/site_src/index.md",
        "gui/scripts/build_site.py",
        "gui/scripts/site_layout.py",
        "gui/docs/distribution.md",
        "gui/docs/CHANGELOG.md",
        "scripts/build_mockup_previews.py",
        "scripts/build_site.py",
        "scripts/validate_site_sources.py",
        "native/bench/power.py",
        "native/new_python_tool.py",
        "gui/new_python_tool.py",
    ],
)
def test_757_reviewed_test_inputs_never_bypass_pytest(path: str) -> None:
    selected = routes([path])
    assert selected["cli"] or selected["docs"], path
    if path.endswith(".py"):
        assert selected["cli"], path


def test_757_every_test_referenced_input_selects_pytest() -> None:
    for path, readers in referenced_inputs().items():
        selected = routes([path])
        assert selected["cli"] or selected["docs"], path
        if not selected["cli"]:
            assert readers <= DOCS_TEST_MODULES, (path, readers)


def test_757_new_test_imports_and_reads_are_discovered_without_a_path_allowlist(
    tmp_path: Path,
) -> None:
    for name in (
        "tests/test_reader.py",
        "gui/site_src/future[raw].md",
        "gui/scripts/future.py",
        "native/bench/future.py",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
    (tmp_path / "tests/test_reader.py").write_text(
        "from pathlib import Path as RootPath\n"
        "from gui.scripts import future\n"
        "ROOT: RootPath = RootPath(__file__).resolve().parents[1]\n"
        "DATA = ROOT / 'gui' / 'site_src' / 'future[raw].md'\n"
        "POWER = ROOT / 'native' / 'bench' / 'future.py'\n"
        "def test_reads_data():\n"
        "    assert DATA.read_text() == ''\n"
    )
    inputs = referenced_inputs(tmp_path)
    for path in (
        "gui/site_src/future[raw].md",
        "gui/scripts/future.py",
        "native/bench/future.py",
    ):
        assert path in inputs
        assert routes([path], repository=tmp_path)["cli"], path


def test_757_deleted_directory_inputs_still_run_their_readers(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "gui").mkdir()
    (tmp_path / "tests/test_copy.py").write_text(
        "import shutil\n"
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "DIRECTORIES = ['gui/site_src']\n"
        "def test_copies(tmp_path):\n"
        "    for name in DIRECTORIES:\n"
        "        shutil.copytree(ROOT / name, tmp_path / name)\n"
    )
    assert routes(["gui/site_src/deleted.md"], repository=tmp_path)["cli"]


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
