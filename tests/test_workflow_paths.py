"""Focused workflow paths regressions."""

from pathlib import Path

import pytest

from scripts.ci_changes import routes
from tests.test_github_actions_policy import (
    AREA_PUSH_WORKFLOWS as AREA_PUSH_WORKFLOWS,
)
from tests.test_github_actions_policy import (
    HEAVY_PUSH_WORKFLOWS as HEAVY_PUSH_WORKFLOWS,
)
from tests.test_github_actions_policy import (
    event_path_filters as event_path_filters,
)
from tests.test_github_actions_policy import (
    github_glob_matches as github_glob_matches,
)
from tests.test_github_actions_policy import (
    github_paths_select as github_paths_select,
)
from tests.test_github_actions_policy import (
    triggered_workflows as triggered_workflows,
)


@pytest.mark.parametrize("action", ["synchronize", "ready_for_review"])
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/wgsextract_cli/core/microarray/__init__.py", True),
        ("src/wgsextract_cli/core/microarray/coordinates.py", True),
        ("src/wgsextract_cli/core/microarray/chm13_calls.py", True),
        ("src/wgsextract_cli/core/microarray/identity.py", True),
        ("src/wgsextract_cli/core/microarray/nested/probes.py", True),
        ("src/wgsextract_cli/core/microarray_targets.py", True),
        ("src/wgsextract_cli/core/microarray/README.md", False),
    ],
)
def test_826_package_moves_preserve_real_chip_concordance_trigger(
    path: str, expected: bool, action: str
) -> None:
    selected = triggered_workflows([path], "pull_request", action=action)
    assert ("remote_resources.yml" in selected) is expected


def test_910_docs_readiness_preserves_path_filters_and_runner_free_cancellation() -> (
    None
):
    assert triggered_workflows(["README.md"], "pull_request") == {"pr_ci.yml"}
    assert triggered_workflows(
        ["README.md"], "pull_request", action="ready_for_review"
    ) == {"pr_ci.yml"}


@pytest.mark.parametrize(
    "paths",
    [
        ["README.md"],
        ["docs/developer_setup.md", "native/README.md"],
        [".github/copilot-instructions.md", "gui/AGENTS.md"],
        ["docs/nested/new-prose.md"],
        ["native/deepvariant/README.md", "gui/app/README.md", "packaging/README.md"],
    ],
)
def test_docs_fast_path_909_does_not_start_heavy_push_ci(paths: list[str]) -> None:
    triggered = triggered_workflows(paths)
    assert triggered == {"gitleaks.yml"}


def test_docs_fast_path_909_keeps_pages_for_handwritten_site_prose() -> None:
    triggered = triggered_workflows(["site_src/new-guide.md"])
    assert triggered == {"gitleaks.yml", "pages.yml"}


@pytest.mark.parametrize(
    "reference",
    [
        "docs/api.md",
        "docs/commands.md",
        "site_src/cli.md",
        "site_src/index.md",
        "site_src/install.md",
        "gui/site_src/index.md",
        "gui/docs/distribution.md",
        "gui/docs/CHANGELOG.md",
    ],
)
def test_docs_fast_path_909_generated_markdown_never_starts_heavy_suites(
    reference: str,
) -> None:
    triggered = triggered_workflows([reference])
    assert not triggered & (HEAVY_PUSH_WORKFLOWS | AREA_PUSH_WORKFLOWS)
    assert "gitleaks.yml" in triggered
    assert triggered_workflows([reference], "pull_request") == {"pr_ci.yml"}
    assert not routes([reference])["cli"]


@pytest.mark.parametrize(
    "code", ["src/wgsextract_cli/main.py", ".github/workflows/pr_ci.yml", "pixi.toml"]
)
def test_docs_fast_path_909_does_not_hide_mixed_code_pushes(code: str) -> None:
    triggered = triggered_workflows(["README.md", code])
    assert HEAVY_PUSH_WORKFLOWS | {"gitleaks.yml"} <= triggered
    assert "os_tests.yml" not in triggered


def test_github_path_filters_reinclude_only_matching_files_in_order() -> None:
    patterns = ["**", "!**/*.md", "docs/api.md", "!docs/private/**"]
    assert github_paths_select(["docs/api.md"], patterns)
    assert not github_paths_select(["README.md", ".github/policy.md"], patterns)
    assert not github_paths_select(["docs/private/script.py"], patterns)
    assert github_paths_select(["README.md", "scripts/check.py"], patterns)
    assert not github_glob_matches("docs/nested/api.md", "docs/*.md")


def test_filter_reader_keeps_push_and_pr_paths_separate(tmp_path: Path) -> None:
    workflow = tmp_path / "filters.yml"
    workflow.write_text(
        "name: filters\non:\n"
        "  push:\n"
        "    branches: [main] # main only\n"
        "    paths:\n"
        '      - "**"\n'
        '      - "!**/*.md"\n'
        '      - "docs/api.md"\n'
        "  pull_request:\n"
        "    types: [opened, synchronize, ready_for_review]\n"
        "    paths-ignore:\n"
        '      - "**/*.md"\n'
        "jobs:\n  test:\n    paths: [unrelated]\n"
    )
    push = event_path_filters(workflow, "push")
    pr = event_path_filters(workflow, "pull_request")
    assert push is not None and pr is not None
    assert github_paths_select(["docs/api.md"], push["paths"])
    assert not github_paths_select(["README.md"], push["paths"])
    assert any(github_glob_matches("docs/api.md", path) for path in pr["paths-ignore"])
    assert event_path_filters(workflow, "merge_group") is None


@pytest.mark.parametrize(
    "events",
    [
        "on: {push: {paths: ['**', '!**/*.md']}, pull_request: null}\n",
        "on:\n    push: {paths: ['**', '!**/*.md']}\n",
        "on:\n    push:\n        paths:\n            - '**'\n            - '!**/*.md'\n",
        "on:\n    push:\n        paths:\n        - '**'\n        - '!**/*.md'\n",
    ],
)
def test_review_911_filter_reader_accepts_flow_and_relative_indentation(
    tmp_path: Path, events: str
) -> None:
    workflow = tmp_path / "flow.yml"
    workflow.write_text(f"name: formatting regression\n{events}jobs: {{}}\n")
    push = event_path_filters(workflow, "push")
    assert push is not None
    assert not github_paths_select(["docs/api.md"], push["paths"])
    assert github_paths_select(["src/main.py"], push["paths"])


@pytest.mark.parametrize("events", ["on: push\n", "on: [push, workflow_dispatch]\n"])
def test_review_911_inline_events_keep_unfiltered_pushes(
    tmp_path: Path, events: str
) -> None:
    workflow = tmp_path / "inline.yml"
    workflow.write_text(events)
    assert event_path_filters(workflow, "push") == {}
    assert event_path_filters(workflow, "pull_request") is None


@pytest.mark.parametrize(
    "paths",
    [
        ["README.md", "docs/developer_setup.md"],
        ["native/README.md", "packaging/windows/new-guide.md"],
        ["gui/app/README.md", "gui/tools/packaging/README.md"],
        [".github/actions/setup-gui/README.md", ".github/workflows/new-guide.md"],
        ["site_src/new-guide.md", "gui/site_src/new-guide.md"],
    ],
)
def test_docs_fast_path_909_pr_prose_runs_only_area_checks(paths: list[str]) -> None:
    assert triggered_workflows(paths, "pull_request") == {"pr_ci.yml"}
    selected = routes(paths)
    assert selected["docs"] == any(
        path.startswith(("site_src/", "gui/site_src/")) for path in paths
    )
    assert not any(selected[area] for area in ("cli", "native", "install"))


@pytest.mark.parametrize(
    "reference", ["docs/api.md", "docs/commands.md", "site_src/cli.md"]
)
def test_docs_fast_path_909_pr_generated_docs_keep_freshness_checks(
    reference: str,
) -> None:
    assert "pr_ci.yml" in triggered_workflows([reference], "pull_request")
    assert routes([reference])["docs"]


def test_docs_fast_path_909_pr_code_still_runs_external_workflows() -> None:
    assert {
        "codeql.yml",
        "gui-ci.yml",
        "gui-dev-setup.yml",
        "gui-distribution.yml",
        "gui-tauri-installer.yml",
    } <= triggered_workflows(
        ["README.md", "gui/app/web/packagers/tauri/src/main.rs"], "pull_request"
    )
