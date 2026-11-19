"""Regression coverage for #914's user-visible selected test list."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_changes import routes
from scripts.ci_select_replay import failed_test_files
from scripts.ci_select_tests import (
    CI_TESTS,
    ci_test_targets,
    os_selection,
    select_tests,
)
from scripts.ci_test_dependencies import ROOT, docs_test_targets


@pytest.mark.parametrize(
    "path",
    [
        "src/wgsextract_cli/core/report_documents.py",
        "scripts/gen_report_schema.py",
        "src/wgsextract_cli/core/personal_report/overview.py",
        "src/wgsextract_cli/core/gene_lists.py",
        "src/wgsextract_cli/assets/schemas/wgsextract.report.1.schema.json",
    ],
)
def test_929_report_contract_inputs_select_committed_freshness(path: str) -> None:
    selected = select_tests([path])
    assert selected == "all" or "tests/test_report_documents.py" in selected


@pytest.mark.parametrize(
    "path",
    [
        "src/wgsextract_cli/core/results.py",
        "src/wgsextract_cli/core/result_schema.py",
        "scripts/gen_output_schemas.py",
        "src/wgsextract_cli/assets/schemas/wgsextract.result.1.schema.json",
    ],
)
def test_929_result_contract_changes_select_freshness(path: str) -> None:
    selected = select_tests([path])
    assert selected == "all" or "tests/test_result_documents.py" in selected


@pytest.mark.parametrize(
    "path",
    [
        "scripts/server_transport_typescript.py",
        "gui/app/shared/generated/server-transport.ts",
        "gui/app/shared/generated/result-records.ts",
        "gui/app/shared/generated/report-records.ts",
    ],
)
def test_989_transport_changes_select_committed_contract_freshness(path: str) -> None:
    assert "tests/test_server_contracts.py" in select_tests([path])


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


def test_914_selector_regressions_do_not_start_the_cli_matrix() -> None:
    paths = ["scripts/ci_select_tests.py", "tests/test_ci_selection.py"]
    assert not routes(paths)["cli"]
    assert set(CI_TESTS) <= set(select_tests(paths))
    assert "tests/test_table_writer.py" not in select_tests(paths)


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


def test_914_real_leaf_module_does_not_select_unrelated_ci_tests() -> None:
    selected = select_tests(["src/wgsextract_cli/commands/benchmark/chip.py"])
    assert "tests/test_microarray_chip_benchmark.py" in selected
    assert "tests/test_ci_ledger.py" not in selected
    assert len(selected) < 20


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


def test_914_transitive_docs_reader_has_an_executable_lane() -> None:
    paths = ["scripts/build_mockup_previews.py"]
    assert "tests/test_mockup_review_edges.py" in select_tests(paths)
    assert routes(paths)["cli"]


def test_914_mixed_product_and_generated_docs_cannot_skip_freshness() -> None:
    selected = select_tests(["tests/test_table_writer.py", "docs/api.md"])
    assert "tests/test_table_writer.py" in selected
    assert "tests/test_api.py" in selected


def test_1132_config_guide_changes_run_generated_reference_freshness() -> None:
    paths = ["docs/data/configuration-guide.md"]
    assert routes(paths)["docs"]
    assert not routes(paths)["cli"]
    assert docs_test_targets(paths) == [
        "tests/test_core_cli.py::test_configuration_docs_are_up_to_date",
        "tests/test_regen.py",
    ]
    assert "tests/test_core_cli.py" in select_tests(paths)


def test_914_ci_observer_change_keeps_its_actual_readers() -> None:
    paths = ["scripts/ci_failures.py"]
    selected = ci_test_targets(paths)
    assert "tests/test_ci_failures.py" in selected
    assert "tests/test_ci_main_observer.py" in selected
    assert set(select_tests(paths)) <= set(selected)
    assert not routes(paths)["cli"]


def test_914_prose_policy_files_do_not_start_ci_tests() -> None:
    paths = [".github/copilot-instructions.md", ".github/README.md"]
    assert select_tests(paths) == []
    assert ci_test_targets(paths) == []
    assert not any(routes(paths).values())


@pytest.mark.parametrize(
    "path",
    [
        "src/wgsextract_cli/core/haplogrep_trees.json",
        "src/wgsextract_cli/ydna/y_str/data.tsv",
    ],
)
def test_914_packaged_resources_cannot_hide_runtime_consumers(path: str) -> None:
    assert select_tests([path]) == "all"


def test_914_collection_errors_are_reported_in_replay() -> None:
    observed = failed_test_files(
        "ERROR tests/test_missing_dependency.py - ImportError: no package\n"
        "FAILED tests/test_runtime.py::test_error - AssertionError\n"
        "ERROR tests/test_no_suffix.py"
    )
    assert observed == {
        "tests/test_missing_dependency.py",
        "tests/test_runtime.py",
        "tests/test_no_suffix.py",
    }


def test_914_explicit_e2e_paths_keep_the_dedicated_runner() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_run_tests.py"),
            "--selection",
            json.dumps(["tests/test_e2e_full_genome.py"]),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "skipping" in result.stdout


def test_914_shared_workflow_change_exercises_all_os_smokes() -> None:
    paths = [".github/workflows/os_tests.yml"]
    matrix = os_selection(paths, select_tests(paths), cli=False)
    assert all(matrix.values())
    assert not routes(paths)["cli"]


def test_923_local_selector_cli_prints_only_diff_selection(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init", "--quiet")
    git("config", "user.name", "CI Test")
    git("config", "user.email", "ci@example.invalid")
    (tmp_path / "README.md").write_text("initial")
    git("add", ".")
    git("commit", "--quiet", "-m", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "README.md").write_text("changed")
    git("add", ".")
    git("commit", "--quiet", "-m", "prose")
    command = [
        sys.executable,
        str(ROOT / "scripts/ci_changes.py"),
        "--base",
        base,
        "--head",
        "HEAD",
        "--print-tests",
    ]
    assert json.loads(subprocess.check_output(command, cwd=tmp_path, text=True)) == []
    assert (
        subprocess.check_output(
            [*command, "--full-suite"], cwd=tmp_path, text=True
        ).strip()
        == "all"
    )
