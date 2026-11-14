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
from scripts.ci_changes import routes
from scripts.ci_test_dependencies import DOCS_TEST_MODULES
from scripts.ci_test_dependencies import test_inputs as referenced_inputs
from scripts.pr_checks_ready import checks_ready, latest_ready_run


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
        "fast": "false",
        "draft_cli": "false",
        "python_files": "[]",
        "routed": "true",
    }


@pytest.mark.parametrize(
    "paths",
    [["src/wgsextract_cli/main.py"], ["pixi.lock"], ["AGENTS.md"], ["unknown/path"]],
)
def test_763_drafts_defer_heavy_checks_until_ready(paths: list[str]) -> None:
    ready = routes(paths)
    draft = routes(paths, draft=True)
    assert draft["docs"] == ready["docs"]
    assert draft["draft_cli"] == ready["cli"]
    assert draft["fast"]
    assert not any(draft[key] for key in ("cli", "native", "site", "install"))
    assert not ready["fast"]
    assert ready["cli"] or ready["docs"]


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
    assert selected["docs"] == "true"
    assert selected["draft_cli"] == str(draft).lower()
    assert selected["cli"] == str(not draft).lower()
    assert selected["native"] == str(not draft).lower()


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
    return {
        "id": run_id,
        "name": "PR area checks",
        "event": "pull_request",
        "head_sha": "same-head",
        "display_title": f"PR area checks / {action} / {'draft' if draft else 'ready'}",
        "created_at": created_at,
        "status": status,
        "conclusion": "success" if status == "completed" else None,
        "pull_requests": [{"number": 802}],
    }


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
        None if redrafted_during_discovery else 20
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
