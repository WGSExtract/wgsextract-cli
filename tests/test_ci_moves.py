"""Real Git diffs exercise the narrow pure-move CI path (#1045)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_changes import queue_windows_required, routes
from scripts.ci_moves import select_moves
from scripts.ci_select_tests import select_tests
from scripts.ci_test_dependencies import ROOT
from tests.ci_routing_helpers import run_router
from tests.test_ci_selection import repository


def test_1045_whole_tree_scanners_do_not_disable_real_repository_moves(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "clone",
            "--shared",
            "--quiet",
            "--config",
            "core.longpaths=true",
            str(ROOT),
            str(root),
        ],
        check=True,
    )
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "CI test")
    registration = root / "src" / "wgsextract_cli" / "cli" / "registration"
    probe = registration / ("ci_move_probe" + ".py")
    target = registration / ("ci_move_relocated" + ".py")
    # Keep the real registry intact: removing a registered handler without
    # updating its lazy target correctly falls back for a different reason.
    shutil.copyfile(registration / ("pet" + ".py"), probe)
    git(root, "add", ".")
    git(root, "commit", "-qm", "real source tree with unreferenced handler")
    base = git(root, "rev-parse", "HEAD")
    git(root, "mv", str(probe), str(target))
    git(root, "commit", "-qm", "byte-identical handler move")

    selected = select_moves(base, "HEAD", repository=root)

    assert selected is not None
    assert "wgsextract_cli.cli.registration.ci_move_relocated" in selected.modules
    assert "tests/test_subprocess_text.py" in selected.tests
    assert "tests/test_text_newlines.py" in selected.tests
    assert len(selected.tests) < len(list((root / "tests").rglob("test_*.py")))


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root, text=True
    ).strip()


def moved_repository(
    root: Path, *, body: str = "VALUE = 1\n", extra: dict[str, str] | None = None
) -> tuple[Path, str]:
    repository(
        root,
        {
            "src/wgsextract_cli/core/old.py": body,
            "src/wgsextract_cli/core/reader.py": "from .old import VALUE\n",
            "tests/test_reader.py": "from wgsextract_cli.core.reader import VALUE\n",
            "tests/test_other.py": "import json\n",
            **(extra or {}),
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
def test_1045_queue_smokes_windows_for_code_but_not_prose(
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
    output = run_router(root, base, "--queue")
    selected = dict(line.split("=", 1) for line in output.splitlines())
    assert selected["windows_tests"] == "[]"
    assert selected["ubuntu_tests"] == "[]"
    assert json.loads(selected["queue_runners"]) == (
        [] if docs_only else ["ubuntu-latest", "windows-latest"]
    )
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


def test_1045_root_package_reexports_are_imported_after_moves(tmp_path: Path) -> None:
    root, base = moved_repository(
        tmp_path, extra={"src/wgsextract_cli/__init__.py": ""}
    )
    (root / "src/wgsextract_cli/__init__.py").write_text(
        "from .core.new import VALUE\n"
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "move and reexport")
    selection = select_moves(base, "HEAD", repository=root)
    assert selection is not None and "wgsextract_cli" in selection.modules
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; runpy.run_path(sys.argv[1])['import_modules'](['wgsextract_cli'])",
            str(ROOT / "scripts/ci_check_moves.py"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "loader",
    [
        "def load():\n    from . import old\n    return old.VALUE\n",
        "def load():\n    import old\n    return old.VALUE\n",
        "from importlib import import_module\ndef load():\n    return import_module('wgsextract_cli.core.old').VALUE\n",
    ],
)
def test_1045_deferred_old_name_consumers_force_normal_selection(
    tmp_path: Path, loader: str
) -> None:
    root, base = moved_repository(
        tmp_path,
        extra={
            "src/wgsextract_cli/core/loader.py": loader,
            "tests/test_loader.py": "from wgsextract_cli.core.loader import load\ndef test_load():\n    assert load() == 1\n",
        },
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "move with unresolved consumer")
    assert select_moves(base, "HEAD", repository=root) is None


def test_1045_renamed_away_source_readers_force_normal_selection(
    tmp_path: Path,
) -> None:
    root, base = moved_repository(
        tmp_path,
        extra={
            "tests/test_source.py": "from pathlib import Path\ndef test_source():\n    assert Path('src/wgsextract_cli/core/old.py').read_text()\n"
        },
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "move with source reader")
    assert select_moves(base, "HEAD", repository=root) is None


def test_1045_whole_tree_globs_keep_scanners_without_forcing_full_fallback(
    tmp_path: Path,
) -> None:
    root, base = moved_repository(
        tmp_path,
        extra={
            "tests/test_scan.py": "from pathlib import Path\nSOURCE = Path(__file__).resolve().parents[1] / 'src'\ndef test_scan():\n    assert list(SOURCE.rglob('*.py'))\n"
        },
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "move under source scanner")

    selected = select_moves(base, "HEAD", repository=root)

    assert selected is not None
    assert "tests/test_reader.py" in selected.tests
    assert "tests/test_scan.py" in selected.tests


def test_1045_narrow_directory_glob_still_forces_normal_selection(
    tmp_path: Path,
) -> None:
    root, base = moved_repository(
        tmp_path,
        extra={
            "tests/test_scan.py": "from pathlib import Path\nSOURCE = Path(__file__).resolve().parents[1] / 'src' / 'wgsextract_cli' / 'core'\ndef test_scan():\n    assert list(SOURCE.glob('*.py'))\n"
        },
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "move under narrower scanner")

    assert select_moves(base, "HEAD", repository=root) is None


def test_1045_opaque_importer_consumers_are_not_omitted(tmp_path: Path) -> None:
    root, base = moved_repository(
        tmp_path,
        extra={
            "src/wgsextract_cli/core/loader.py": "from importlib import import_module\ndef load(name):\n    return import_module(name)\n",
            "tests/test_loader.py": "from wgsextract_cli.core.loader import load\n",
        },
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "move beside opaque importer")
    selected = select_moves(base, "HEAD", repository=root)
    assert selected is not None
    assert "tests/test_loader.py" in selected.tests


def test_1045_autouse_fixture_consumers_run_after_import_rewrites(
    tmp_path: Path,
) -> None:
    root, base = moved_repository(
        tmp_path,
        extra={
            "tests/conftest.py": "import pytest\nfrom wgsextract_cli.core.reader import VALUE\n@pytest.fixture(autouse=True)\ndef isolate():\n    assert VALUE == 1\n"
        },
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "move behind fixture")
    selected = select_moves(base, "HEAD", repository=root)
    assert selected is not None
    assert "tests/test_other.py" in selected.tests


def test_1045_new_code_without_discovered_consumers_still_requires_queue_windows(
    tmp_path: Path,
) -> None:
    path = "src/wgsextract_cli/core/unreferenced.py"
    root = repository(
        tmp_path, {path: "VALUE = 1\n", "tests/test_other.py": "import json\n"}
    )
    assert select_tests([path], repository=root) == []
    assert not routes([path], repository=root)["cli"]
    assert queue_windows_required([path])
