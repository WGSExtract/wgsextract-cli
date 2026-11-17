"""User-visible size-ratchet failures, maintenance and scan boundaries."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_python_size as size_guard
from scripts.ci_changes import routes
from scripts.ci_select_tests import ci_test_targets
from scripts.ci_test_dependencies import docs_test_targets

CHECKER = Path(__file__).resolve().parents[1] / "scripts/check_python_size.py"


def baseline(root, ceilings):
    file = root / "scripts/python_size_baseline.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"version": 1, "files": ceilings}))
    return file


def module(root, name, lines):
    file = root / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("pass\n" * lines)
    return file


def check(root, *args):
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root), *args],
        capture_output=True,
        text=True,
    )


def test_826_size_guard_rejects_new_megafiles_and_existing_growth(tmp_path):
    baseline(tmp_path, {"src/existing.py": 920})
    module(tmp_path, "src/existing.py", 790)
    file = module(tmp_path, "tests/new.py", 800)
    assert check(tmp_path).returncode == 0
    module(tmp_path, "src/existing.py", 920)
    assert check(tmp_path).returncode == 0

    file.write_text(file.read_text() + "pass\n")
    result = check(tmp_path)
    assert result.returncode == 1
    assert "tests/new.py" in result.stdout
    assert "python_size_exceeded" in result.stdout
    file.unlink()

    module(tmp_path, "src/existing.py", 921)
    result = check(tmp_path)
    assert result.returncode == 1
    assert "src/existing.py" in result.stdout and "ceiling 920" in result.stdout


def test_826_refresh_lowers_budgets_and_removes_finished_or_deleted_outliers(tmp_path):
    file = baseline(
        tmp_path,
        {"src/reduced.py": 1000, "tests/split.py": 950, "scripts/deleted.py": 900},
    )
    module(tmp_path, "src/reduced.py", 850)
    module(tmp_path, "tests/split.py", 500)
    stale = check(tmp_path)
    assert stale.returncode == 1
    assert "unrelated ceilings" in stale.stdout
    result = check(tmp_path, "--refresh")
    assert result.returncode == 0, result.stdout
    assert json.loads(file.read_text())["files"] == {"src/reduced.py": 850}

    module(tmp_path, "src/reduced.py", 851)
    assert check(tmp_path).returncode == 1


def test_826_refresh_cannot_grandfather_new_or_growing_modules(tmp_path):
    file = baseline(tmp_path, {"src/existing.py": 900})
    original = file.read_bytes()
    module(tmp_path, "src/existing.py", 901)
    module(tmp_path, "scripts/new.py", 801)
    assert check(tmp_path, "--refresh").returncode == 1
    assert file.read_bytes() == original


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
def test_826_size_scan_excludes_gui_and_scratch_and_counts_last_lines(tmp_path, ending):
    baseline(tmp_path, {})
    module(tmp_path, "gui/tool.py", 1500)
    module(tmp_path, "out/capture.py", 1500)
    module(tmp_path, "scratch/experiment.py", 1500)
    for folder in (
        ".env",
        ".env.local",
        ".venv",
        ".tox",
        ".nox",
        "env",
        "venv",
        "ENV",
        "env.bak",
        "venv.bak",
    ):
        module(tmp_path, f"scripts/{folder}/lib/third_party.py", 1500)
    file = module(tmp_path, "src/portable.py", 0)
    file.write_bytes((b"pass" + ending) * 799 + b"pass")
    assert check(tmp_path).returncode == 0
    nested = module(tmp_path, "src/scratch/real_module.py", 801)
    assert "src/scratch/real_module.py" in check(tmp_path).stdout
    nested.unlink()
    file.write_bytes(file.read_bytes() + ending + b"pass")
    result = check(tmp_path)
    assert result.returncode == 1
    assert "801 lines" in result.stdout


@pytest.mark.parametrize(
    "content",
    [
        "{",
        '{"version": 2, "files": {}}',
        '{"version": 1, "files": {"../outside.py": 900}}',
        '{"version": 1, "files": {"src/anything.py": true}}',
    ],
)
def test_826_malformed_size_baselines_fail_instead_of_disabling_the_guard(
    tmp_path, content
):
    baseline(tmp_path, {}).write_text(content)
    result = check(tmp_path)
    assert result.returncode == 2
    assert "python_size_invalid_baseline" in result.stderr


@pytest.mark.parametrize(
    "path",
    [
        "scripts/check_python_size.py",
        "scripts/python_size_baseline.json",
        "tests/test_ci_landing.py",
        "tests/test_ci_required.py",
        "tests/test_workflow_readiness.py",
        "tests/test_workflow_paths.py",
    ],
)
def test_826_split_policy_and_size_guard_changes_keep_real_ci_coverage(path):
    assert not routes([path])["cli"]
    assert {
        "tests/test_ci_landing.py",
        "tests/test_ci_required.py",
        "tests/test_workflow_readiness.py",
        "tests/test_workflow_paths.py",
        "tests/test_python_size.py",
    } <= set(ci_test_targets([path]))


def test_826_split_policy_readers_remain_in_the_documentation_lane():
    selected = set(
        docs_test_targets(["scripts/ci_changes.py", ".github/workflows/pr_ci.yml"])
    )
    assert {
        "tests/test_ci_landing.py",
        "tests/test_ci_required.py",
        "tests/test_workflow_readiness.py",
        "tests/test_workflow_paths.py",
    } <= selected


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("baselined", [False, True])
def test_826_unreadable_subtrees_fail_without_rewriting_budgets(
    tmp_path, monkeypatch, capsys, refresh, baselined
):
    name = "src/private/large.py"
    file = baseline(tmp_path, {name: 900} if baselined else {})
    original = file.read_bytes()
    module(tmp_path, name, 901)
    blocked = tmp_path / "src/private"
    real_scandir = os.scandir

    def unreadable(path):
        if not isinstance(path, int) and Path(path) == blocked:
            raise PermissionError(f"unreadable_subtree: {blocked}")
        return real_scandir(path)

    with monkeypatch.context() as boundary:
        boundary.setattr(os, "scandir", unreadable)
        flags = ["--root", str(tmp_path), *(["--refresh"] if refresh else [])]
        assert size_guard.main(flags) == 2
        assert "unreadable_subtree" in capsys.readouterr().err
    assert file.read_bytes() == original


@pytest.mark.parametrize(
    "path",
    [
        "gui/app/web/packagers/tauri/src/main.rs",
        "gui/app/web/src/client/view.ts",
        "gui/app/shared/config.ts",
    ],
)
def test_826_policy_fixture_paths_do_not_start_gui_only_docs_jobs(path):
    assert not routes([path])["docs"]
    assert docs_test_targets([path]) == []
