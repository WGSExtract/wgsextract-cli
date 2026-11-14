import re
from pathlib import Path

import pytest

from scripts import validate_github_actions as policy
from scripts.ci_changes import routes


@pytest.mark.parametrize(
    "workflow",
    [
        "ubuntu_ci.yml",
        "macos_ci.yml",
        "windows_ci.yml",
        "linux_arm64.yml",
    ],
)
def test_906_selected_tests_survive_shared_workflow_boundary(workflow: str) -> None:
    source = (policy.ROOT / ".github/workflows" / workflow).read_text()
    # CI contract: the shared suite must receive both PR selection and smoke mode.
    assert "uses: ./.github/workflows/os_tests.yml" in source
    assert "tests: ${{ inputs.tests }}" in source
    assert "smoke-only: ${{ inputs.smoke-only }}" in source


@pytest.mark.parametrize("job", ["required", "ready-gate"])
def test_876_summary_gates_stop_when_the_workflow_is_cancelled(job: str) -> None:
    workflow = (policy.ROOT / ".github/workflows/pr_ci.yml").read_text()
    block = workflow.split(f"\n  {job}:\n", 1)[1].split("\n  required:", 1)[0]
    condition = block.split("    if:", 1)[1].split("    runs-on:", 1)[0]
    # CI contract: !cancelled() overrides implicit success() without keeping
    # gates alive on workflow cancellation like always() does.
    assert "!cancelled()" in condition
    assert "always()" not in condition
    assert "success()" not in condition


def write_workflow(root: Path, uses: str) -> None:
    directory = root / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "test.yml").write_text(f"jobs:\n  test:\n    steps:\n      - {uses}\n")


def test_dependabot_can_update_pin_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_workflow(
        tmp_path,
        "uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0",
    )
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    assert policy.validate_workflows() == []


@pytest.mark.parametrize(
    ("uses", "error"),
    [
        ("uses: actions/checkout@v7.0.1 # v7.0.1", "full commit SHA"),
        ("uses: actions/checkout@3d3c42e # v7.0.1", "full commit SHA"),
        (
            "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "version comment",
        ),
        (
            "uses: unknown/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
            "ALLOWED_ACTIONS",
        ),
    ],
)
def test_remote_action_policy_rejects_unsafe_or_unreviewed_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    uses: str,
    error: str,
) -> None:
    write_workflow(tmp_path, uses)
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    assert any(error in item for item in policy.validate_workflows())


def test_composite_actions_are_held_to_the_same_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    action = tmp_path / ".github" / "actions" / "apt-install"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(
        "runs:\n  using: composite\n  steps:\n"
        "    - uses: actions/cache/restore@v6.1.0 # v6.1.0\n"
    )
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    assert any("full commit SHA" in item for item in policy.validate_workflows())


@pytest.mark.parametrize(
    ("uses", "wrapper"),
    [
        (
            "prefix-dev/setup-pixi@9936258fee697aff89b3fed6ca1f80a382ef694e # v0.11.3",
            "setup-pixi",
        ),
        (
            "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0",
            "setup-gui",
        ),
    ],
)
def test_wrapped_actions_are_used_only_through_the_shared_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uses: str, wrapper: str
) -> None:
    """Issue 822: direct steps bypassed the shared pin and cache policy."""
    uses = f"uses: {uses}"
    write_workflow(tmp_path, uses)
    action = tmp_path / ".github" / "actions" / wrapper
    action.mkdir(parents=True)
    (action / "action.yml").write_text(
        f"runs:\n  using: composite\n  steps:\n    - {uses}\n"
    )
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    errors = policy.validate_workflows()
    assert len(errors) == 1
    assert "test.yml" in errors[0] and f"./.github/actions/{wrapper}" in errors[0]


@pytest.mark.parametrize(
    ("queue", "cancel", "valid"),
    [("max", "false", True), ("max", "true", False), ("all", "false", False)],
)
def test_new_github_queue_schema_is_validated_despite_actionlint_lag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    queue: str,
    cancel: str,
    valid: bool,
) -> None:
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "test.yml").write_text(
        f"concurrency:\n  group: ci-observer\n  queue: {queue}\n"
        f"  cancel-in-progress: {cancel}\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
    )
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    errors = policy.validate_workflows()
    assert (not errors) == valid
    if not valid:
        assert any("queue" in error for error in errors)


CLI_PUSH_WORKFLOWS = {
    "ubuntu_ci.yml",
    "macos_ci.yml",
    "windows_ci.yml",
    "linux_arm64.yml",
}
HEAVY_PUSH_WORKFLOWS = CLI_PUSH_WORKFLOWS | {
    "cli_smoke.yml",
    "linux_aligners.yml",
    "linux_deepvariant.yml",
    "gatk_windows.yml",
    "gkl_windows.yml",
    "gui-tauri-installer.yml",
    "codeql.yml",
}


def github_glob_matches(path: str, pattern: str) -> bool:
    """Evaluate the *, ** subset used here, including root files and dotfiles."""
    assert not any(char in pattern for char in "?[]+\\")
    expression = ""
    index = 0
    while index < len(pattern):
        if pattern[index : index + 3] == "**/":
            expression += "(?:.*/)?"
            index += 3
        elif pattern[index : index + 2] == "**":
            expression += ".*"
            index += 2
        elif pattern[index] == "*":
            expression += "[^/]*"
            index += 1
        else:
            expression += re.escape(pattern[index])
            index += 1
    return re.fullmatch(expression, path) is not None


def github_paths_select(paths: list[str], patterns: list[str]) -> bool:
    """GitHub paths are ordered: the last matching pattern decides per file."""
    for path in paths:
        selected = False
        for pattern in patterns:
            if github_glob_matches(path, pattern.removeprefix("!")):
                selected = not pattern.startswith("!")
        if selected:
            return True
    return False


def event_path_filters(workflow: Path, event: str) -> dict[str, list[str]] | None:
    """Read Actions' block event/list subset without adding docs-job dependencies."""
    text = workflow.read_text()
    on = re.search(r"^(?:on|'on'|\"on\"):\s*(?:#.*)?$", text, re.MULTILINE)
    assert on is not None, f"Unsupported event syntax in {workflow.name}"
    lines = text[on.end() :].splitlines()
    in_event = False
    filters: dict[str, list[str]] = {}
    list_key = None
    for line in lines:
        content = line.partition("#")[0].rstrip()
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip())
        if indent == 0:
            break
        if indent == 2:
            if in_event:
                break
            in_event = content.strip() == f"{event}:"
            continue
        if not in_event:
            continue
        if indent == 4:
            key, _, value = content.strip().partition(":")
            list_key = (
                key
                if key in {"branches", "branches-ignore", "paths", "paths-ignore"}
                else None
            )
            if list_key is not None:
                filters[list_key] = []
                if value.strip():
                    value = value.strip()
                    assert value.startswith("[") and value.endswith("]"), workflow.name
                    filters[list_key] = [
                        item.strip().strip("'\"") for item in value[1:-1].split(",")
                    ]
                    list_key = None
        elif list_key is not None:
            assert indent == 6 and content.strip().startswith("- "), workflow.name
            filters[list_key].append(content.strip()[2:].strip("'\""))
    return filters if in_event else None


def triggered_workflows(paths: list[str], event: str = "push") -> set[str]:
    selected = set()
    for workflow in (policy.ROOT / ".github/workflows").glob("*.yml"):
        push = event_path_filters(workflow, event)
        if push is None:
            continue
        assert not ("paths" in push and "paths-ignore" in push), workflow.name
        if "branches" in push and not github_paths_select(["main"], push["branches"]):
            continue
        if "branches-ignore" in push and github_paths_select(
            ["main"], push["branches-ignore"]
        ):
            continue
        if "paths" in push and not github_paths_select(paths, push["paths"]):
            continue
        if "paths-ignore" in push and all(
            any(github_glob_matches(path, pattern) for pattern in push["paths-ignore"])
            for path in paths
        ):
            continue
        selected.add(workflow.name)
    return selected


@pytest.mark.parametrize(
    "paths",
    [
        ["README.md"],
        ["docs/developer_setup.md", "native/README.md"],
        [".github/copilot-instructions.md", "gui/AGENTS.md"],
        ["docs/nested/new-prose.md"],
    ],
)
def test_docs_fast_path_909_does_not_start_heavy_push_ci(paths: list[str]) -> None:
    triggered = triggered_workflows(paths)
    assert not triggered & HEAVY_PUSH_WORKFLOWS
    assert "gitleaks.yml" in triggered
    assert "pages.yml" not in triggered


def test_docs_fast_path_909_keeps_pages_for_handwritten_site_prose() -> None:
    triggered = triggered_workflows(["site_src/new-guide.md"])
    assert {"gitleaks.yml", "pages.yml"} <= triggered
    assert not triggered & HEAVY_PUSH_WORKFLOWS


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
    assert not triggered & HEAVY_PUSH_WORKFLOWS
    assert "gitleaks.yml" in triggered
    assert triggered_workflows([reference], "pull_request") == {"pr_ci.yml"}
    assert not routes([reference])["cli"]


@pytest.mark.parametrize(
    "code", ["src/wgsextract_cli/main.py", ".github/workflows/pr_ci.yml", "pixi.toml"]
)
def test_docs_fast_path_909_does_not_hide_mixed_code_pushes(code: str) -> None:
    triggered = triggered_workflows(["README.md", code])
    assert HEAVY_PUSH_WORKFLOWS | {"gitleaks.yml"} <= triggered


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
