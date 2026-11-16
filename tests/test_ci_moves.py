"""Real Git diffs exercise the narrow pure-move CI path (#1045)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_moves import select_moves
from scripts.ci_test_dependencies import ROOT
from tests.test_ci_selection import repository


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root, text=True
    ).strip()


def moved_repository(root: Path, *, body: str = "VALUE = 1\n") -> tuple[Path, str]:
    repository(
        root,
        {
            "src/wgsextract_cli/core/old.py": body,
            "src/wgsextract_cli/core/reader.py": "from .old import VALUE\n",
            "tests/test_reader.py": "from wgsextract_cli.core.reader import VALUE\n",
            "tests/test_other.py": "import json\n",
        },
    )
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "CI test")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    git(root, "mv", "src/wgsextract_cli/core/old.py", "src/wgsextract_cli/core/new.py")
    (root / "src/wgsextract_cli/core/reader.py").write_text("from .new import VALUE\n")
    return root, base


def test_1045_pure_move_selects_consumers_instead_of_deleted_module_fallback(
    tmp_path: Path,
) -> None:
    root, base = moved_repository(tmp_path)
    git(root, "add", ".")
    git(root, "commit", "-qm", "move")
    selected = select_moves(base, "HEAD", repository=root)
    assert selected is not None
    assert selected.tests == ["tests/test_reader.py"]
    assert "wgsextract_cli.core.new" in selected.modules
    assert "wgsextract_cli.core.reader" in selected.modules


def test_1045_synthetic_merge_checkout_with_the_same_tree_still_proves_moves(
    tmp_path: Path,
) -> None:
    root, base = moved_repository(tmp_path)
    git(root, "add", ".")
    git(root, "commit", "-qm", "move")
    head = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-qb", "merge-checkout", base)
    git(root, "merge", "--no-ff", "-qm", "synthetic merge", head)
    assert git(root, "rev-parse", "HEAD") != head
    selected = select_moves(base, head, repository=root)
    assert selected is not None
    assert selected.tests == ["tests/test_reader.py"]


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("src/wgsextract_cli/core/reader.py", "from .new import VALUE\nVALUE += 1\n"),
        ("src/wgsextract_cli/core/new.py", "VALUE = 2\n"),
        ("src/wgsextract_cli/core/reader.py", "def broken(\n"),
        ("pixi.toml", "[workspace]\n"),
        ("tests/test_reader.py", "assert False\n"),
    ],
)
def test_1045_uncertain_or_behavior_changing_moves_keep_normal_selection(
    tmp_path: Path, path: str, text: str
) -> None:
    root, base = moved_repository(tmp_path)
    (root / path).write_text(text)
    git(root, "add", ".")
    git(root, "commit", "-qm", "not a pure move")
    assert select_moves(base, "HEAD", repository=root) is None


def test_1045_import_only_change_without_a_move_keeps_normal_selection(
    tmp_path: Path,
) -> None:
    root, base = moved_repository(tmp_path)
    git(root, "mv", "src/wgsextract_cli/core/new.py", "src/wgsextract_cli/core/old.py")
    git(root, "add", ".")
    git(root, "commit", "-qm", "imports only")
    assert select_moves(base, "HEAD", repository=root) is None


@pytest.mark.parametrize("docs_only", [True, False])
def test_1045_queue_runs_full_windows_for_code_but_not_prose(
    tmp_path: Path, docs_only: bool
) -> None:
    root, base = moved_repository(tmp_path)
    if docs_only:
        git(
            root,
            "mv",
            "src/wgsextract_cli/core/new.py",
            "src/wgsextract_cli/core/old.py",
        )
        (root / "src/wgsextract_cli/core/reader.py").write_text(
            "from .old import VALUE\n"
        )
        (root / "README.md").write_text("Documentation\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "queue group")
    output = subprocess.check_output(
        [
            sys.executable,
            str(ROOT / "scripts/ci_changes.py"),
            "--base",
            base,
            "--head",
            "HEAD",
            "--full-suite",
        ],
        cwd=root,
        text=True,
    )
    selected = dict(line.split("=", 1) for line in output.splitlines())
    assert selected["windows_tests"] == ("[]" if docs_only else "all")
    assert selected["ubuntu_tests"] == "all"
    assert selected["move_modules"] == "[]"


def test_1045_import_failure_cannot_pass_move_validation() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_check_moves.py"),
            "--modules",
            '["wgsextract_cli.missing_move_target"]',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr
