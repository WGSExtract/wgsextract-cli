"""Regression coverage for area routing and small-PR landing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import pr_checks_ready
from scripts.ci_changes import routes, site_validation
from scripts.ci_select_tests import ci_path, select_tests
from scripts.ci_test_dependencies import (
    DOCS_ONLY_SCRIPTS,
    DOCS_TEST_TARGETS,
    GENERATED_DOCS,
    docs_test_targets,
)
from scripts.ci_test_dependencies import test_inputs as referenced_inputs
from scripts.pr_checks_ready import checks_ready, latest_ready_run


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
        "docs_tests": "[]",
        "site_validation": "false",
        "native": "false",
        "site": "false",
        "install": "true",
        "fast": "false",
        "draft_cli": "false",
        "python_files": "[]",
        "routed": "true",
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
            "tests/test_ci_selection.py",
            "tests/test_github_actions_policy.py",
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
            "scripts/ci_changes.py",
            ["tests/test_ci_changes.py", "tests/test_github_actions_policy.py"],
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
    checks = [
        {
            "name": "Ready PR area checks",
            "bucket": buckets[0] if buckets else "pending",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/10/job/100",
        },
        {
            "name": "Draft lint and fast tests",
            "bucket": "skipping",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/10/job/101",
        },
        {
            "name": "changes",
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/10/job/102",
        },
        {
            "name": "required",
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/10/job/103",
        },
        *[{"bucket": bucket} for bucket in buckets[1:]],
    ]
    assert checks_ready(checks, run_id=10) is ready


def test_802_old_draft_passes_and_skips_cannot_land_before_ready_ci_registers() -> None:
    checks = [
        {
            "name": "Draft lint and fast tests",
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/10/job/100",
        },
        *[
            {"name": name, "bucket": "skipping"}
            for name in ("ubuntu", "macos", "windows")
        ],
        *[{"name": name, "bucket": "pass"} for name in ("changes", "lint", "docs")],
    ]
    assert not checks_ready(checks, run_id=10)


@pytest.mark.parametrize(
    ("gate_bucket", "draft_bucket", "routing_run", "ready"),
    [
        ("pending", "skipping", 11, False),
        ("pass", "pass", 11, False),
        ("pass", "skipping", 10, False),
        ("pass", "skipping", 11, True),
    ],
)
def test_802_landing_requires_completed_non_draft_validation_from_one_run(
    gate_bucket: str, draft_bucket: str, routing_run: int, ready: bool
) -> None:
    checks = [
        {
            "name": "Ready PR area checks",
            "bucket": gate_bucket,
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/11/job/110",
        },
        {
            "name": "Draft lint and fast tests",
            "bucket": draft_bucket,
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/11/job/111",
        },
        {
            "name": "changes",
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/{routing_run}/job/112",
        },
        {
            "name": "required",
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": "https://github.com/owner/repo/actions/runs/11/job/113",
        },
    ]
    assert checks_ready(checks, run_id=11) is ready


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


def test_910_readiness_validates_without_any_preceding_draft_run() -> None:
    assert (
        latest_ready_run(
            [],
            head_sha="same-head",
            ready_since="2026-11-13T16:20:00Z",
            pr_number=802,
        )
        is None
    )
    assert (
        latest_ready_run(
            [area_run(13, "2026-11-13T16:20:02Z")],
            head_sha="same-head",
            ready_since="2026-11-13T16:20:00Z",
            pr_number=802,
        )
        == 13
    )


@pytest.mark.parametrize("new_run_registered", [False, True])
def test_802_redrafting_the_same_head_cannot_reuse_validation_before_latest_readiness(
    new_run_registered: bool,
) -> None:
    runs = [
        area_run(10, "2026-11-13T16:00:00Z"),
        area_run(11, "2026-11-13T16:10:00Z", action="labeled", draft=True),
        area_run(12, "2026-11-13T16:20:01Z", action="labeled"),
    ]
    if new_run_registered:
        runs.append(area_run(13, "2026-11-13T16:20:02Z"))
    expected_run = latest_ready_run(
        runs,
        head_sha="same-head",
        ready_since="2026-11-13T16:20:00Z",
        pr_number=802,
    )
    assert expected_run == (13 if new_run_registered else None)
    old_checks = [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/10/job/{job}",
        }
        for job, name in enumerate(("changes", "required", "Ready PR area checks"), 100)
    ]
    assert not checks_ready(old_checks, run_id=expected_run)
    if new_run_registered:
        fresh_checks = [
            {**check, "link": check["link"].replace("/runs/10/", "/runs/13/")}
            for check in old_checks
        ]
        assert checks_ready(fresh_checks, run_id=expected_run)


@pytest.mark.parametrize("status", ["queued", "in_progress", "completed"])
def test_802_newest_ready_run_must_finish_without_falling_back_to_an_older_success(
    status: str,
) -> None:
    unrelated = area_run(22, "2026-11-13T16:21:02Z")
    unrelated["head_sha"] = "another-head"
    other_pr = area_run(23, "2026-11-13T16:21:03Z")
    other_pr["pull_requests"] = [{"number": 999}]
    queue_run = area_run(24, "2026-11-13T16:21:04Z")
    queue_run["event"] = "merge_group"
    queue_run["display_title"] = "PR area checks / checks_requested / queue"
    runs = [
        area_run(20, "2026-11-13T16:20:01Z"),
        area_run(21, "2026-11-13T16:21:01Z", action="synchronize", status=status),
        unrelated,
        other_pr,
        queue_run,
    ]
    assert latest_ready_run(
        runs,
        head_sha="same-head",
        ready_since="2026-11-13T16:20:00Z",
        pr_number=802,
    ) == (21 if status == "completed" else None)


@pytest.mark.parametrize("redrafted_during_discovery", [False, True])
def test_802_ready_transition_changes_during_validation_fail_closed(
    monkeypatch: pytest.MonkeyPatch, redrafted_during_discovery: bool
) -> None:
    snapshot = {
        "headRefOid": "same-head",
        "isDraft": False,
        "createdAt": "2026-11-13T16:00:00Z",
        "timelineItems": {
            "nodes": [{"id": "ready-1", "createdAt": "2026-11-13T16:20:00Z"}]
        },
    }
    after = {
        **snapshot,
        "timelineItems": {
            "nodes": [{"id": "ready-2", "createdAt": "2026-11-13T16:30:00Z"}]
        },
    }
    snapshots = iter([snapshot, after if redrafted_during_discovery else snapshot])

    def github_response(*arguments: str) -> Any:
        if arguments[:2] == ("api", "graphql"):
            return {"data": {"repository": {"pullRequest": next(snapshots)}}}
        return [{"workflow_runs": [area_run(20, "2026-11-13T16:20:01Z")]}]

    monkeypatch.setattr(pr_checks_ready, "github_json", github_response)
    assert pr_checks_ready.current_ready_run(802, "owner/repo") == (
        (None, [])
        if redrafted_during_discovery
        else (20, [area_run(20, "2026-11-13T16:20:01Z")])
    )


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
            "FORCE_INSTALL": "false",
            "LABEL_EVENT": "false",
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


@LINUX_BASH
@pytest.mark.parametrize(
    ("routing_result", "routed", "failed", "cancelled", "ready"),
    [
        ("success", "", "false", "false", False),
        ("failure", "true", "true", "false", False),
        ("success", "true", "true", "false", False),
        ("success", "true", "false", "true", False),
        ("success", "true", "false", "false", True),
    ],
)
def test_802_ready_gate_fails_for_missing_routing_or_failed_selected_checks(
    routing_result: str, routed: str, failed: str, cancelled: str, ready: bool
) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            workflow_shell("Require complete routing and successful selected checks"),
        ],
        env={
            **os.environ,
            "ROUTING_RESULT": routing_result,
            "ROUTED": routed,
            "CHECKS_FAILED": failed,
            "CHECKS_CANCELLED": cancelled,
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is ready


@LINUX_BASH
@pytest.mark.parametrize(
    ("routing_result", "routed", "ubuntu_result", "ready"),
    [
        ("success", "true", "success", True),
        ("success", "", "success", False),
        ("failure", "true", "success", False),
        ("success", "true", "failure", False),
        ("success", "true", "cancelled", False),
    ],
)
def test_802_queue_required_summary_accepts_its_small_matrix_but_not_missing_routing(
    routing_result: str, routed: str, ubuntu_result: str, ready: bool
) -> None:
    needs = {
        "changes": {"result": routing_result},
        "ubuntu": {"result": ubuntu_result},
        "gitleaks": {"result": "success"},
        **{
            job: {"result": "skipped"}
            for job in ("draft-fast", "docs", "python-lint", "native-smoke", "pages")
        },
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            workflow_shell("Require routing and every selected required job to pass"),
        ],
        env={**os.environ, "NEEDS": json.dumps(needs), "ROUTED": routed},
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is ready


@pytest.mark.parametrize("required_bucket", [None, "pending", "skipping", "pass"])
def test_802_enqueue_requires_the_passing_required_check_from_the_current_pr_run(
    required_bucket: str | None,
) -> None:
    checks = [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/10/job/{job}",
        }
        for job, name in enumerate(("changes", "Ready PR area checks"), 100)
    ]
    if required_bucket:
        checks.append(
            {
                "name": "required",
                "bucket": required_bucket,
                "workflow": "PR area checks",
                "link": "https://github.com/owner/repo/actions/runs/10/job/102",
            }
        )
    assert checks_ready(checks, run_id=10) is (required_bucket == "pass")


@pytest.mark.parametrize(
    ("workflow", "failed_run", "bucket", "ready"),
    [
        ("PR area checks", 10, "cancel", True),
        ("PR area checks", 10, "fail", True),
        ("PR area checks", 11, "cancel", False),
        ("PR area checks", 11, "fail", False),
        ("Secret Scan", 10, "cancel", False),
        ("Secret Scan", 10, "fail", False),
    ],
)
def test_802_cancelled_draft_checks_do_not_poison_the_selected_ready_run(
    workflow: str, failed_run: int, bucket: str, ready: bool
) -> None:
    checks = [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/11/job/{job}",
        }
        for job, name in enumerate(("changes", "required", "Ready PR area checks"), 110)
    ]
    checks.extend(
        [
            {
                "name": "Draft lint and fast tests${{ label suffix }}",
                "bucket": "skipping",
                "workflow": "PR area checks",
                "link": "https://github.com/owner/repo/actions/runs/11/job/113",
            },
            {
                "name": "Draft lint and fast tests",
                "bucket": bucket,
                "workflow": workflow,
                "link": f"https://github.com/owner/repo/actions/runs/{failed_run}/job/100",
            },
        ]
    )
    assert checks_ready(checks, run_id=11) is ready


def test_757_landing_cli_rejects_an_empty_check_report() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "pr_checks_ready.py"
    result = subprocess.run(
        [sys.executable, str(script), "--pr", "802"],
        input=json.dumps([]),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


@pytest.mark.parametrize("latest_conclusion", ["success", "failure", "cancelled", None])
@pytest.mark.parametrize("reported", [False, True])
def test_876_superseded_workflow_checks_do_not_hide_the_latest_result(
    latest_conclusion: str | None,
    reported: bool,
) -> None:
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/ci_failures/cancelled_draft_gate.json"
        ).read_text()
    )
    draft = fixture["run"]
    ready = {
        **draft,
        "id": draft["id"] + 1,
        "display_title": "PR area checks / ready_for_review / ready",
        "conclusion": "success",
        "pull_requests": [{"number": 875}],
        "created_at": "2026-11-14T09:20:00Z",
    }
    scan = {
        **draft,
        "id": draft["id"] + 2,
        "workflow_id": 123,
        "path": ".github/workflows/gitleaks.yml",
    }
    replacement = {
        **scan,
        "id": scan["id"] + 1,
        "conclusion": latest_conclusion,
        "status": "completed" if latest_conclusion else "queued",
    }
    buckets = {"success": "pass", "failure": "fail", "cancelled": "cancel"}
    checks = [
        {
            "name": job["name"],
            "bucket": buckets[job["conclusion"]],
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/{draft['id']}/job/{i}",
        }
        for i, job in enumerate(fixture["jobs"])
    ]
    checks += [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/{ready['id']}/job/{i}",
        }
        for i, name in enumerate(("changes", "required", "Ready PR area checks"), 20)
    ]
    checks.append(
        {
            "name": "gitleaks",
            "bucket": "cancel",
            "workflow": "Secret Scan",
            "link": f"https://github.com/owner/repo/actions/runs/{scan['id']}/job/30",
        }
    )
    # Queued replacement deliberately has no registered check yet.
    if latest_conclusion:
        checks.append(
            {
                **checks[-1],
                "bucket": buckets[latest_conclusion],
                "link": f"https://github.com/owner/repo/actions/runs/{replacement['id']}/job/31",
            }
        )
    if not reported:
        checks = [check for check in checks if check["workflow"] == "PR area checks"]
    assert checks_ready(
        checks, run_id=ready["id"], runs=[replacement, draft, scan, ready]
    ) is (not reported or latest_conclusion == "success")
    assert checks_ready(checks, run_id=ready["id"], runs=[draft, ready]) is (
        not reported
    )


@pytest.mark.parametrize(
    "link",
    [
        "",
        "https://external.example/check/30",
        "https://github.com/owner/repo/actions/runs/not-a-run/job/30",
        "https://github.com/owner/repo/actions/runs/999/job/30",
    ],
)
def test_876_unverifiable_passing_check_cannot_establish_readiness(link: str) -> None:
    ready = {**area_run(20, "2026-11-13T16:20:01Z"), "workflow_id": 416978936}
    checks = [
        {
            "name": name,
            "bucket": "pass",
            "workflow": "PR area checks",
            "link": f"https://github.com/owner/repo/actions/runs/20/job/{i}",
        }
        for i, name in enumerate(("changes", "required", "Ready PR area checks"))
    ]
    checks.append(
        {"name": "unverified", "bucket": "pass", "workflow": "Other", "link": link}
    )
    assert not checks_ready(checks, run_id=20, runs=[ready])


def test_876_recorded_pr_run_with_empty_associations_is_still_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/ci_failures/cancelled_draft_gate.json"
        ).read_text()
    )
    run = {
        **fixture["run"],
        "display_title": "PR area checks / ready_for_review / ready",
        "conclusion": "success",
    }
    pr = {
        "headRefOid": run["head_sha"],
        "headRefName": run["head_branch"],
        "headRepository": {"nameWithOwner": run["head_repository"]["full_name"]},
        "isDraft": False,
        "createdAt": run["created_at"],
        "timelineItems": {"nodes": []},
    }

    def response(*arguments: str) -> Any:
        if arguments[:2] == ("api", "graphql"):
            return {"data": {"repository": {"pullRequest": pr}}}
        return [{"workflow_runs": [run]}]

    monkeypatch.setattr(pr_checks_ready, "github_json", response)
    assert pr_checks_ready.current_ready_run(875, "WGSExtract/wgsextract-cli") == (
        run["id"],
        [run],
    )


@LINUX_BASH
@pytest.mark.parametrize("dependency_result", ["failure", "cancelled"])
def test_876_active_run_cannot_pass_with_a_failed_or_timed_out_dependency(
    dependency_result: str,
) -> None:
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/ci_failures/cancelled_draft_gate.json"
        ).read_text()
    )
    needs = {
        job["name"]: {"result": "success"}
        for job in fixture["jobs"]
        if job["name"] != "required"
    }
    needs["gitleaks / gitleaks"]["result"] = dependency_result
    result = subprocess.run(
        [
            "bash",
            "-c",
            workflow_shell("Require routing and every selected required job to pass"),
        ],
        env={**os.environ, "NEEDS": json.dumps(needs), "ROUTED": "true"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "gitleaks / gitleaks" in result.stderr


def test_867_ready_run_found_by_workflow_file_not_run_name() -> None:
    """#867: the Actions API reports pr_ci.yml's run-name as `name`, so every
    PR was rejected. The ready run is found by its workflow file, and a run of
    another workflow with the same title is not taken for it."""
    ready = area_run(30, "2026-11-13T16:21:00Z", action="synchronize")
    other_workflow = area_run(31, "2026-11-13T16:22:00Z", action="synchronize")
    other_workflow["path"] = ".github/workflows/ubuntu_ci.yml"
    found = pr_checks_ready.latest_ready_run(
        [ready, other_workflow],
        head_sha="same-head",
        ready_since="2026-11-13T16:20:00Z",
        pr_number=802,
    )
    assert found == 30
