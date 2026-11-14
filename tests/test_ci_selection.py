"""Regression coverage for #914's user-visible selected test list."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_select_tests import CI_TESTS, os_selection, select_tests
from scripts.ci_test_dependencies import ROOT


def repository(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_914_single_module_selects_transitive_and_relative_imports(
    tmp_path: Path,
) -> None:
    root = repository(
        tmp_path,
        {
            "src/wgsextract_cli/core/changed.py": "VALUE = 1",
            "src/wgsextract_cli/core/reader.py": "from . import changed",
            "tests/test_reader.py": "from wgsextract_cli.core import reader",
            "tests/test_unrelated.py": "import json",
        },
    )
    assert select_tests(["src/wgsextract_cli/core/changed.py"], repository=root) == [
        "tests/test_reader.py"
    ]


@pytest.mark.parametrize(
    "path",
    [
        "tests/conftest.py",
        "tests/htslib_helpers.py",
        "tests/fixtures/sample.bam",
        "tests/cli_snapshot.py",
        "pyproject.toml",
        "pixi.toml",
        "pixi.lock",
        "unknown/config",
    ],
)
def test_914_shared_inputs_fail_open(path: str) -> None:
    assert select_tests([path]) == "all"


def test_914_workflow_only_selects_ci_tests_and_only_changed_os_smoke() -> None:
    paths = [".github/workflows/macos_ci.yml"]
    selection = select_tests(paths)
    assert selection == sorted(CI_TESTS)
    matrix = os_selection(paths, selection, cli=False)
    assert matrix["macos"]
    assert not any(matrix[os] for os in ("ubuntu", "windows", "arm64"))


def test_914_lazy_registry_command_selects_its_tests(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            "src/wgsextract_cli/commands/extract.py": "VALUE = 1",
            "src/wgsextract_cli/commands/info.py": "VALUE = 1",
            "src/wgsextract_cli/cli/registration/extract.py": "from wgsextract_cli.cli._lazy import command_handler as handler\n"
            'cmd = handler("wgsextract_cli.commands.extract", "cmd_extract")',
            "tests/test_extract.py": "from wgsextract_cli.cli.registration import extract",
            "tests/test_info.py": "from wgsextract_cli.commands import info",
        },
    )
    assert select_tests(
        ["src/wgsextract_cli/commands/extract.py"], repository=root
    ) == ["tests/test_extract.py"]


def test_914_unresolved_cli_import_selects_all(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            "src/wgsextract_cli/commands/extract.py": "VALUE = 1",
            "src/wgsextract_cli/main.py": "from importlib import import_module\nimport_module(command_name)",
            "tests/test_main.py": "from wgsextract_cli.main import main",
            "tests/test_unrelated.py": "import json",
        },
    )
    assert (
        select_tests(["src/wgsextract_cli/commands/extract.py"], repository=root)
        == "all"
    )


def test_914_finite_startup_registry_reaches_lazy_command(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            "src/wgsextract_cli/commands/extract.py": "VALUE = 1",
            "src/wgsextract_cli/commands/registry.py": (
                "from importlib import import_module\n"
                "class CommandRegistration:\n"
                " def register(self):\n"
                '  import_module(f"wgsextract_cli.cli.registration.{self.module}")\n'
                'COMMAND_MODULES = tuple(CommandRegistration(name) for name in ("extract",))'
            ),
            "src/wgsextract_cli/cli/registration/extract.py": (
                "from wgsextract_cli.cli._lazy import command_handler\n"
                'cmd = command_handler("wgsextract_cli.commands.extract", "cmd_extract")'
            ),
            "src/wgsextract_cli/main.py": "from .commands.registry import COMMAND_MODULES",
            "tests/test_main.py": "from wgsextract_cli.main import main",
            "tests/test_other.py": "import json",
        },
    )
    assert select_tests(
        ["src/wgsextract_cli/commands/extract.py"], repository=root
    ) == ["tests/test_main.py"]


def test_914_dynamic_relative_import_uses_explicit_package(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            "src/wgsextract_cli/core/changed.py": "VALUE = 1",
            "src/wgsextract_cli/commands/reader.py": (
                "from importlib import import_module as load\n"
                'load(".changed", "wgsextract_cli.core")'
            ),
            "tests/test_reader.py": "from wgsextract_cli.commands import reader",
            "tests/test_other.py": "import json",
        },
    )
    assert select_tests(["src/wgsextract_cli/core/changed.py"], repository=root) == [
        "tests/test_reader.py"
    ]


def test_914_literal_file_input_selects_reader(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            "reference/input.csv": "value",
            "tests/test_reader.py": 'from pathlib import Path\nPath("reference/input.csv").read_text()',
            "tests/test_other.py": "import json",
        },
    )
    assert select_tests(["reference/input.csv"], repository=root) == [
        "tests/test_reader.py"
    ]


def test_914_conftest_source_dependency_selects_fixture_users(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            "src/wgsextract_cli/core/config.py": "VALUE = 1",
            "src/wgsextract_cli/commands/extract.py": "VALUE = 1",
            "tests/conftest.py": "from tests.helpers import isolate",
            "tests/helpers.py": (
                "def isolate():\n from wgsextract_cli.core import config\n"
                "def unused():\n from wgsextract_cli.commands import extract\n"
            ),
            "tests/test_isolation.py": "import json",
        },
    )
    assert select_tests(["src/wgsextract_cli/core/config.py"], repository=root) == [
        "tests/test_isolation.py"
    ]


def test_914_assets_do_not_hide_indirect_runtime_consumers() -> None:
    assert (
        select_tests(["src/wgsextract_cli/assets/reference/seed_genomes.csv"]) == "all"
    )


def test_914_sibling_helper_file_inputs_propagate_to_importers(tmp_path: Path) -> None:
    root = repository(
        tmp_path,
        {
            "gui/mockups/serve.py": "",
            "tests/test_reader.py": 'from pathlib import Path\nSCRIPT = Path("gui/mockups/serve.py")',
            "tests/test_importer.py": "from test_reader import SCRIPT",
            "tests/test_unrelated.py": "import json",
        },
    )
    assert select_tests(["gui/mockups/serve.py"], repository=root) == [
        "tests/test_importer.py",
        "tests/test_reader.py",
    ]


def test_914_real_lazy_command_and_startup_tests_are_selected() -> None:
    selected = select_tests(["src/wgsextract_cli/commands/extract.py"])
    assert selected != "all"
    assert "tests/test_extract_commands.py" in selected
    assert "tests/test_cli_parser.py" in selected


def test_914_empty_selection_skips_without_starting_pixi() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_run_tests.py"),
            "--selection",
            json.dumps([]),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "skipping" in result.stdout
