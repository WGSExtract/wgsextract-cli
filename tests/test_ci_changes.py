"""Regression coverage for area routing and small-PR landing."""

from __future__ import annotations

import json as json
import os as os
import subprocess as subprocess
import sys as sys
from pathlib import Path as Path
from pathlib import PurePosixPath as PurePosixPath
from typing import Any as Any

import pytest as pytest

from scripts import pr_checks_ready as pr_checks_ready
from scripts.ci_changes import routes as routes
from scripts.ci_changes import setup_checks as setup_checks
from scripts.ci_changes import site_validation as site_validation
from scripts.ci_codeql_scope import EXTENSIONS as EXTENSIONS
from scripts.ci_codeql_scope import LANGUAGES as LANGUAGES
from scripts.ci_codeql_scope import languages as codeql_languages
from scripts.ci_optional_tools import routed_suites as routed_suites
from scripts.ci_select_tests import ci_path as ci_path
from scripts.ci_select_tests import ci_test_targets as ci_test_targets
from scripts.ci_select_tests import select_tests as select_tests
from scripts.ci_test_dependencies import DOCS_ONLY_SCRIPTS as DOCS_ONLY_SCRIPTS
from scripts.ci_test_dependencies import DOCS_TEST_TARGETS as DOCS_TEST_TARGETS
from scripts.ci_test_dependencies import GENERATED_DOCS as GENERATED_DOCS
from scripts.ci_test_dependencies import docs_test_targets as docs_test_targets
from scripts.ci_test_dependencies import test_inputs as referenced_inputs
from scripts.pr_checks_ready import checks_ready as checks_ready
from scripts.pr_checks_ready import latest_ready_run as latest_ready_run


@pytest.mark.parametrize(
    ("paths", "enabled"),
    [
        (
            ["AGENTS.md", "native/README.md", ".github/copilot-instructions.md"],
            set(),
        ),
        (["docs/api.md", "docs/commands.md"], {"docs"}),
        (["native/bwa/bwa.c"], {"native"}),
        # Mockup changes also feed the cross-platform review-server regressions.
        (["gui/mockups/index.html", "site_src/index.md"], {"site", "cli"}),
        (["gui/scripts/site_layout.py"], {"site", "cli"}),
        (["gui/app/shared/config.ts"], set()),
        (["src/wgsextract_cli/commands/info.py"], {"cli"}),
        (["pixi.lock"], {"cli", "install"}),
        (["scripts/installlab/lab.py"], {"cli", "install"}),
        ([".github/actions/download-cache/action.yml"], {"docs"}),
        (["install.sh"], {"cli", "install"}),
        (["uninstall.sh"], {"cli", "install"}),
        (["uninstall_windows.bat"], {"cli", "install"}),
        (["scripts/bootstrap_windows_prereqs.ps1"], {"cli", "install"}),
        (["src/wgsextract_cli/main.py"], {"cli", "install"}),
        (["gui/app/web/packagers/tauri/Cargo.lock"], {"install"}),
        (
            ["native/bwa/bwa.c", "src/wgsextract_cli/main.py"],
            {"native", "cli", "install"},
        ),
        (["unknown/new-config"], {"cli"}),
    ],
)
def test_757_routes_isolated_and_mixed_prs(paths: list[str], enabled: set[str]) -> None:
    assert {key for key, value in routes(paths).items() if value} == enabled


def test_757_label_forces_install_lab_on_a_docs_pr() -> None:
    assert routes(["README.md"], force_install_lab=True)["install"]


def test_954_install_override_on_an_empty_diff_does_not_select_the_cli_matrix() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "ci_changes.py"
    output = subprocess.check_output(
        [
            sys.executable,
            str(script),
            "--base",
            "HEAD",
            "--head",
            "HEAD",
            "--force-install-lab",
        ],
        text=True,
    )
    assert dict(line.split("=") for line in output.splitlines()) == {
        "cli": "false",
        "docs": "false",
        "docs_tests": "[]",
        "site_validation": "false",
        "native": "false",
        "site": "false",
        "install": "true",
        "fast": "false",
        "draft_cli": "false",
        "python_files": "[]",
        "routed": "true",
        "setup_checks": "true",
        "move_modules": "[]",
        "optional_tests": "[]",
        "tests": "[]",
        "ci": "false",
        "ci_tests": "[]",
        "generic_tests": "[]",
        "ubuntu_tests": "[]",
        "macos_tests": "[]",
        "windows_tests": "[]",
        "arm64_tests": "[]",
        "ubuntu_smoke": "false",
        "macos_smoke": "false",
        "windows_smoke": "false",
        "arm64_smoke": "false",
    }


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ("src/wgsextract_cli/core/contamination.py", "tests/test_contamination.py"),
        (
            "src/wgsextract_cli/commands/qc/contamination.py",
            "tests/test_contamination.py",
        ),
        ("tests/test_contamination.py", "tests/test_contamination.py"),
        ("src/wgsextract_cli/commands/vcf/callers.py", "tests/test_vcf_callers.py"),
        ("tests/test_methylation.py", "tests/test_methylation.py"),
        (
            "src/wgsextract_cli/commands/methylation_hp.py",
            "tests/test_methylation_regions.py",
        ),
    ],
)
def test_1199_optional_tools_follow_affected_test_imports(
    changed: str, expected: str
) -> None:
    selected = select_tests([changed])
    assert expected in selected
    assert expected in routed_suites([changed], selected)


def test_1199_optional_lane_does_not_allocate_for_unrelated_changes() -> None:
    paths = ["tests/test_bwa_mem2.py"]
    assert routed_suites(paths, select_tests(paths)) == []
    assert routed_suites(["README.md"], []) == []
    assert "tests/test_contamination.py" in routed_suites(
        ["scripts/ci_optional_tools.py"], []
    )


@pytest.mark.parametrize(
    "changed", [".github/workflows/pr_ci.yml", "scripts/ci_changes.py"]
)
def test_1199_optional_caller_and_router_changes_exercise_integrations(
    changed: str,
) -> None:
    assert "tests/test_contamination.py" in routed_suites(
        [changed], select_tests([changed])
    )


@pytest.mark.parametrize(
    ("changed", "contamination"),
    [
        ("README.md", False),
        ("gui/app/shared/config.ts", False),
        ("src/wgsextract_cli/core/contamination.py", True),
    ],
)
def test_1199_queue_full_os_suite_keeps_optional_tools_diff_routed(
    tmp_path: Path, changed: str, contamination: bool
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], text=True
        ).strip()

    git("init", "--quiet")
    git("config", "user.name", "CI routing test")
    git("config", "user.email", "ci@example.invalid")
    git("commit", "--quiet", "--allow-empty", "-m", "base")
    base = git("rev-parse", "HEAD")
    path = tmp_path / changed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed\n")
    git("add", changed)
    git("commit", "--quiet", "-m", "change")
    script = Path(__file__).resolve().parents[1] / "scripts/ci_changes.py"
    output = subprocess.check_output(
        [
            sys.executable,
            str(script),
            "--base",
            base,
            "--head",
            "HEAD",
            "--full-suite",
        ],
        cwd=tmp_path,
        text=True,
    )
    selected = dict(line.split("=", 1) for line in output.splitlines())
    assert selected["ubuntu_tests"] == "all"
    optional = json.loads(selected["optional_tests"])
    assert ("tests/test_contamination.py" in optional) is contamination
    if not contamination:
        assert optional == []


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="workflow steps run in Linux bash"
)
@pytest.mark.parametrize("live_label", ["true", "false", "api-error"])
def test_954_routing_reads_live_install_label_and_fails_closed(
    tmp_path: Path, live_label: str
) -> None:
    root = Path(__file__).resolve().parents[1]
    command = workflow_shell("Route the complete PR diff")
    # GitHub's API is the network boundary; git diff and routing run for real.
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        + ("exit 1\n" if live_label == "api-error" else f"echo {live_label}\n")
    )
    gh.chmod(0o755)
    output = tmp_path / "output"
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "BASE": "HEAD",
        "HEAD": "HEAD",
        "PR_NUMBER": "954",
        "GITHUB_REPOSITORY": "WGSExtract/wgsextract-cli",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "DRAFT": "false",
        "FULL_SUITE": "false",
    }
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if live_label == "api-error":
        assert result.returncode != 0
        assert not output.exists()
    else:
        assert result.returncode == 0, result.stderr
        selected = dict(line.split("=", 1) for line in output.read_text().splitlines())
        assert selected["install"] == live_label
        assert selected["routed"] == "true"


@pytest.mark.parametrize(
    "paths",
    [["src/wgsextract_cli/main.py"], ["pixi.lock"], ["AGENTS.md"], ["unknown/path"]],
)
def test_763_drafts_defer_heavy_checks_until_ready(paths: list[str]) -> None:
    ready = routes(paths)
    draft = routes(paths, draft=True)
    assert draft["docs"] == (bool(docs_test_targets(paths)) or site_validation(paths))
    assert draft["draft_cli"] == ready["cli"]
    assert draft["fast"] == any(
        ready[area] for area in ("cli", "native", "site", "install")
    )
    assert not any(draft[key] for key in ("cli", "native", "site", "install"))
    assert not ready["fast"]


def test_763_install_override_cannot_start_heavy_checks_on_a_draft() -> None:
    assert not routes(["install.sh"], draft=True, force_install_lab=True)["install"]
    assert not any(routes([], draft=True, force_install_lab=True).values())


@pytest.mark.parametrize(
    "path",
    [
        "site_src/cli.md",
        "site_src/index.md",
        "site_src/install.md",
        "gui/site_src/index.md",
        "gui/scripts/build_site.py",
        "gui/scripts/site_layout.py",
        "scripts/build_mockup_previews.py",
        "scripts/build_site.py",
        "native/bench/power.py",
        "native/new_python_tool.py",
        "gui/new_python_tool.py",
    ],
)
def test_757_reviewed_test_inputs_never_bypass_pytest(path: str) -> None:
    selected = routes([path])
    assert selected["cli"] or selected["docs"], path
    if path.endswith(".py") and path not in DOCS_ONLY_SCRIPTS:
        assert selected["cli"], path


def test_757_every_test_referenced_input_selects_pytest() -> None:
    for path, readers in referenced_inputs().items():
        if ci_path(path):
            assert not routes([path])["cli"], path
            selected_tests = select_tests([path])
            actual_readers = {
                reader for reader in readers if Path(reader).name.startswith("test_")
            }
            assert selected_tests == "all" or actual_readers <= set(selected_tests), (
                path
            )
            continue
        readers = readers - {
            "tests/test_ci_changes.py",
            "tests/test_ci_landing.py",
            "tests/test_ci_required.py",
            "tests/test_ci_selection.py",
            "tests/test_github_actions_policy.py",
            "tests/test_workflow_readiness.py",
            "tests/test_workflow_paths.py",
            "tests/test_python_size.py",
        }
        readers = frozenset(
            reader for reader in readers if Path(reader).name.startswith("test_")
        )
        if not readers:
            continue
        if path.endswith(".md") and path not in GENERATED_DOCS:
            continue
        if path in GENERATED_DOCS:
            assert docs_test_targets([path]), path
            continue
        selected = routes([path])
        assert selected["cli"] or selected["docs"], path
        if not selected["cli"]:
            assert readers <= {target.split("::")[0] for target in DOCS_TEST_TARGETS}, (
                path,
                readers,
            )


def test_757_new_test_imports_and_reads_are_discovered_without_a_path_allowlist(
    tmp_path: Path,
) -> None:
    for name in (
        "tests/test_reader.py",
        "gui/site_src/future[raw].json",
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
        "DATA = ROOT / 'gui' / 'site_src' / 'future[raw].json'\n"
        "POWER = ROOT / 'native' / 'bench' / 'future.py'\n"
        "def test_reads_data():\n"
        "    assert DATA.read_text() == ''\n"
    )
    inputs = referenced_inputs(tmp_path)
    for path in (
        "gui/site_src/future[raw].json",
        "gui/scripts/future.py",
        "native/bench/future.py",
    ):
        assert path in inputs
        assert routes([path], repository=tmp_path)["cli"], path


def test_1326_drive_like_literals_never_stat_outside_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "gui").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_reader.py").write_text(
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "ERROR = 'E: Failed to fetch http://mirror/pool'\n"
        "LOG = Path(ERROR)\n"
        "DRIVE = ROOT / 'E:' / 'pool'\n"
        "DATA = 'gui/future.json'\n"
    )
    root = tmp_path.resolve()
    drive_like = root / "E: Failed to fetch http:"
    stat = os.stat

    def repository_stat(
        path: str | os.PathLike[str], *args: Any, **kwargs: Any
    ) -> os.stat_result:
        # Off Windows this prefix stays in the repository, so flag the probe
        # itself; is_dir() can't swallow an AssertionError.
        if Path(path) == drive_like:
            raise AssertionError(f"statted drive-like text: {path}")
        # An unready Windows drive raises WinError 1005 instead of "missing".
        if not Path(path).is_relative_to(root):
            raise OSError(1005, "unrecognized volume", path)
        return stat(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", repository_stat)
        inputs = referenced_inputs(tmp_path)
    assert "gui/future.json" in inputs
    if sys.platform == "win32":  # elsewhere "E:" is an ordinary in-repo name
        assert not any(path.startswith("E:") for path in inputs)


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
    assert routes(["gui/site_src/deleted.json"], repository=tmp_path)["cli"]


@pytest.mark.parametrize("draft", [False, True])
def test_757_diff_routes_both_sides_of_a_cross_area_rename(
    tmp_path: Path, draft: bool
) -> None:
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
        [
            sys.executable,
            str(script),
            "--base",
            base,
            "--head",
            "HEAD",
            *(["--draft"] if draft else []),
        ],
        cwd=tmp_path,
        text=True,
    )
    selected = dict(line.split("=", 1) for line in output.splitlines())
    assert selected["fast"] == str(draft).lower()
    assert selected["docs"] == "false"
    assert selected["draft_cli"] == str(draft).lower()
    assert selected["cli"] == str(not draft).lower()
    assert selected["native"] == str(not draft).lower()


@pytest.mark.parametrize(
    ("paths", "validate"),
    [
        (["README.md", "docs/developer_setup.md", "gui/docs/CHANGELOG.md"], False),
        (["site_src/index.md", "site_src/install.md"], True),
        (["gui/site_src/index.md"], True),
        (["native/deepvariant/README.md"], False),
    ],
)
def test_913_prose_skips_python_tests_and_only_validates_site_sources(
    paths: list[str], validate: bool
) -> None:
    for draft in (False, True):
        selected = routes(paths, draft=draft)
        assert not any(
            selected[area] for area in ("cli", "native", "site", "install", "fast")
        )
        assert selected["docs"] == validate
        assert site_validation(paths) == validate
        assert docs_test_targets(paths) == []


@pytest.mark.parametrize(
    ("path", "targets"),
    [
        ("docs/api.md", ["tests/test_api.py::test_api_docs_are_up_to_date"]),
        ("docs/commands.md", ["tests/test_cli_help.py"]),
        ("site_src/cli.md", ["tests/test_cli_help.py"]),
        ("scripts/regen.py", ["tests/test_regen.py"]),
        ("scripts/bump_version.py", ["tests/test_bump_version.py"]),
        (
            "scripts/gen_api_docs.py",
            ["tests/test_api.py::test_api_docs_are_up_to_date", "tests/test_regen.py"],
        ),
        (
            "scripts/gen_command_docs.py",
            ["tests/test_cli_help.py", "tests/test_regen.py"],
        ),
        (
            "docs/configuration.md",
            ["tests/test_core_cli.py::test_configuration_docs_are_up_to_date"],
        ),
        (
            "scripts/gen_config_docs.py",
            [
                "tests/test_core_cli.py::test_configuration_docs_are_up_to_date",
                "tests/test_regen.py",
            ],
        ),
        (
            "scripts/ci_changes.py",
            [
                "tests/test_ci_changes.py",
                "tests/test_ci_landing.py",
                "tests/test_ci_required.py",
                "tests/test_github_actions_policy.py",
                "tests/test_workflow_readiness.py",
                "tests/test_workflow_paths.py",
            ],
        ),
        (
            "scripts/build_mockup_previews.py",
            ["tests/test_build_mockup_previews.py"],
        ),
        ("tests/fixtures/api_cli_snapshot.json", ["tests/test_api_cli_parity.py"]),
    ],
)
def test_913_light_checks_run_only_for_their_own_inputs(
    path: str, targets: list[str]
) -> None:
    selected = routes([path])
    if path == "scripts/build_mockup_previews.py":
        # Its non-light imported readers now run in the product lane.
        assert selected["cli"]
    else:
        assert not selected["cli"]
        assert selected["docs"]
    assert docs_test_targets([path]) == targets


@pytest.mark.parametrize(
    "paths",
    [
        ["src/wgsextract_cli/commands/info.py"],
        ["src/wgsextract_cli/api/__init__.py", "docs/api.md"],
        ["src/wgsextract_cli/cli/help.py", "docs/commands.md", "site_src/cli.md"],
    ],
)
def test_913_cli_lane_owns_freshness_without_duplicate_docs_pytest(
    paths: list[str],
) -> None:
    selected = routes(paths)
    assert selected["cli"]
    assert docs_test_targets(paths, cli=selected["cli"]) == []
    assert selected["docs"] == site_validation(paths)


def area_run(
    run_id: int,
    created_at: str,
    *,
    action: str = "ready_for_review",
    draft: bool = False,
    status: str = "completed",
) -> dict[str, Any]:
    title = f"PR area checks / {action} / {'draft' if draft else 'ready'}"
    # The Actions API reports the run-name as both `name` and `display_title`.
    return {
        "id": run_id,
        "name": title,
        "path": ".github/workflows/pr_ci.yml",
        "event": "pull_request",
        "head_sha": "same-head",
        "display_title": title,
        "created_at": created_at,
        "status": status,
        "conclusion": "success" if status == "completed" else None,
        "pull_requests": [{"number": 802}],
    }


# GitHub Actions runs these workflow steps in Linux's bash 5. On Windows "bash"
# is WSL's launcher, with no distribution installed, and macOS's bash 3.2
# fails on an empty "${flags[@]}" under `set -u`.
LINUX_BASH = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="workflow steps run in Linux bash"
)


def workflow_shell(step_name: str) -> str:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pr_ci.yml"
    ).read_text()
    step = workflow.split(f"      - name: {step_name}\n", 1)[1]
    lines = step.split("        run: |\n", 1)[1].splitlines()
    command = []
    for line in lines:
        if line and not line.startswith("          "):
            break
        command.append(line[10:])
    return "\n".join(command)


@LINUX_BASH
def test_914_ci_policy_creates_out_on_a_fresh_checkout(tmp_path: Path) -> None:
    test = tmp_path / "tests" / "test_policy_input.py"
    test.parent.mkdir()
    test.write_text("def test_temp_input(tmp_path):\n assert tmp_path.is_dir()\n")
    script = workflow_shell("Run affected CI policy and routing tests")
    # The Python environment is already installed; exercise local bootstrap,
    # not the package-install network boundary.
    script = "\n".join(
        line for line in script.splitlines() if "pip install" not in line
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "CI_TESTS": json.dumps(["tests/test_policy_input.py"])},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "ci-policy").is_dir()


@LINUX_BASH
@pytest.mark.parametrize("base", ["HEAD", "not-a-git-revision"])
def test_802_router_exception_cannot_be_hidden_by_the_summary_pipeline(
    tmp_path: Path, base: str
) -> None:
    output = tmp_path / "outputs"
    result = subprocess.run(
        ["bash", "-c", workflow_shell("Route the complete PR diff")],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "BASE": base,
            "HEAD": "HEAD",
            "PR_NUMBER": "",
            "DRAFT": "false",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        },
        capture_output=True,
        text=True,
    )
    if base == "HEAD":
        assert result.returncode == 0
        assert "routed=true" in output.read_text()
    else:
        assert result.returncode != 0
        assert not output.exists()


@pytest.mark.parametrize(
    ("path", "selected"),
    [
        ("src/wgsextract_cli/commands/info.py", False),
        ("src/wgsextract_cli/core/alignment.py", False),
        ("src/wgsextract_cli/cli/parser.py", True),
        ("src/wgsextract_cli/cli/registration/align.py", True),
        ("gui/app/shared/config.ts", True),
        ("packaging/windows/pin.py", True),
        ("install.sh", True),
        ("pixi.lock", True),
        (".github/workflows/os_tests.yml", True),
        ("gui/README.md", False),
    ],
)
def test_1045_setup_heavy_checks_follow_their_inputs(path: str, selected: bool) -> None:
    assert setup_checks([path]) is selected


def test_1229_release_helper_edits_run_only_the_ci_policy_lane() -> None:
    paths = [
        "scripts/ci_release_latest.py",
        "tests/test_ci_release_latest.py",
        ".github/workflows/release.yml",
    ]
    for changed in [*([path] for path in paths), paths]:
        assert not routes(changed)["cli"], changed
        assert "tests/test_ci_release_latest.py" in ci_test_targets(changed), changed


def test_1373_main_push_dedupe_edits_run_the_cache_regressions() -> None:
    for path in ("scripts/ci_merge_group_passes.py", "scripts/ci_run_tests.py"):
        assert not routes([path])["cli"], path
        assert "tests/test_ci_test_cache.py" in ci_test_targets([path]), path
    workflow = [".github/workflows/os_tests.yml"]
    assert "tests/test_github_actions_policy.py" in ci_test_targets(workflow)


CODEQL_SCOPE = Path(__file__).resolve().parents[1] / "scripts/ci_codeql_scope.py"


def codeql_scope(repo: Path, *args: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, str(CODEQL_SCOPE), *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    key, _, value = result.stdout.strip().partition("=")
    assert key == "analyze"
    languages: list[str] = json.loads(value)
    return languages


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ({"src/pkg/mod.py": "x = 2\n"}, ["python"]),
        ({"docs/guide.md": "changed\n"}, []),
        ({"tests/fixtures/data.json": "{}\n"}, ["javascript-typescript"]),
        ({"crate/src/main.rs": "fn main() {}\n"}, ["rust"]),
        # Non-Rust crate inputs (configs, icons) may change what Rust embeds.
        ({"crate/tauri.conf.json": "{}\n"}, ["javascript-typescript", "rust"]),
        ({"crate/icons/icon.png": "png\n"}, ["rust"]),
        (
            {"tools/check/action.yml": "runs: {}\n"},
            ["javascript-typescript", "actions"],
        ),
        (
            {".github/codeql/config.txt": "x\n"},
            ["python", "javascript-typescript", "rust", "actions"],
        ),
    ],
)
def test_1373_codeql_pr_analyzes_only_the_languages_it_touches(
    tmp_path: Path, changed: dict[str, str], expected: list[str]
) -> None:
    def commit(files: dict[str, str]) -> str:
        for name, text in files.items():
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(text)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "c",
            ],
            cwd=tmp_path,
            check=True,
        )
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
        ).strip()

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    base = commit({"src/pkg/mod.py": "x = 1\n", "crate/Cargo.toml": "[package]\n"})
    head = commit(changed)
    assert codeql_scope(tmp_path, "--base", base, "--head", head) == expected
    # Pushes and the nightly schedule keep every language.
    assert codeql_scope(tmp_path, "--all") == [
        "python",
        "javascript-typescript",
        "rust",
        "actions",
    ]


def test_1373_root_level_crate_treats_every_file_as_rust_input() -> None:
    assert set(LANGUAGES) == set(EXTENSIONS)
    root = {PurePosixPath(".")}
    assert codeql_languages(["assets/logo.png"], root) == ["rust"]
    assert codeql_languages(["assets/logo.png"], {PurePosixPath("crate")}) == []
