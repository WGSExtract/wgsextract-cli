import ast
import json
import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import cast

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
    assert "tests: ${{ inputs.tests || 'all' }}" in source
    assert "smoke-only: ${{ inputs.smoke-only == true }}" in source


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


@pytest.mark.parametrize(
    "workflow", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name
)
def test_1038_started_main_pushes_are_never_cancelled(workflow: Path) -> None:
    if event_path_filters(workflow, "push") is None:
        pytest.skip("No push trigger")
    if workflow.name == "mockup_previews.yml":
        pytest.skip("Only mockups branches, not main")
    concurrency = workflow_concurrency(workflow)
    if not concurrency:
        return
    # CI contract: only PR events may cancel started runs; main keeps one pending.
    cancel = concurrency["cancel-in-progress"]
    assert cancel in {
        "false",
        "${{ github.event_name == 'pull_request' }}",
        "${{ inputs.tag == '' && github.event_name == 'pull_request' }}",
    }, workflow.name


def test_1038_shared_os_callers_run_all_tiers_without_shard_fanout() -> None:
    suite = (WORKFLOWS / "os_tests.yml").read_text()
    default = suite.split("      matrix:\n", 1)[1].split("      reference:\n", 1)[0]
    entries = json.loads(default.split("default: >-\n", 1)[1])
    assert len(entries) == 1
    assert entries[0]["tier"] == entries[0]["shard"] == "all"
    for workflow in WORKFLOWS.glob("*.yml"):
        source = workflow.read_text()
        if "uses: ./.github/workflows/os_tests.yml" not in source:
            continue
        overrides = re.findall(r"^      matrix: '(.+)'$", source, re.MULTILINE)
        for override in overrides:
            entries = json.loads(override)
            assert len(entries) == 1, workflow.name
            assert entries[0]["tier"] == entries[0]["shard"] == "all"
    assert "writer: ${{ strategy.job-index == 0 }}" in suite
    assert "--cache-record" in suite and "--cache-read" in suite
    assert "matrix.tier == 'integration'" not in suite


def test_1038_smoke_shares_setup_and_preserves_first_failure_evidence() -> None:
    source = (WORKFLOWS / "cli_smoke.yml").read_text()
    assert "matrix.check" not in source
    assert "check: [package, benchmark]" not in source
    assert source.count("uses: ./.github/actions/setup-pixi") == 1
    assert "run: pixi run package-smoke" in source
    benchmark = source.split("      - name: Run Benchmark\n", 1)[1]
    # A second failed step could mask a package regression as a known test flake.
    assert "if:" not in benchmark.split("      - name:", 1)[0]
    assert "tests/test_smoke_real_full.py --ci-require-real-data" in source


def test_1045_draft_review_request_never_executes_pr_code() -> None:
    source = (WORKFLOWS / "copilot_review_collaborators.yml").read_text()
    assert "pull_request_target:" in source
    assert "!github.event.pull_request.draft" not in source
    assert "author_association" in source
    assert "COLLABORATOR" in source and "MEMBER" in source
    assert "actions/checkout" not in source
    assert "gh pr edit" in source and "--add-reviewer @copilot" in source
    assert "permissions: {}" in source


@pytest.mark.parametrize(
    "workflow",
    [
        "ubuntu_ci.yml",
        "macos_ci.yml",
        "windows_ci.yml",
        "linux_arm64.yml",
        "cli_smoke.yml",
    ],
)
def test_1045_nightly_full_matrix_is_gated_before_setup(workflow: str) -> None:
    source = (WORKFLOWS / workflow).read_text()
    assert "uses: ./.github/workflows/nightly_guard.yml" in source
    assert f"workflow: {workflow}" in source
    jobs = re.split(r"^  ([\w-]+):\s*\n", source.split("\njobs:\n", 1)[1], flags=re.M)
    for name, body in zip(jobs[1::2], jobs[2::2], strict=True):
        if name == "nightly":
            continue
        assert "needs: nightly" in body
        assert "needs.nightly.outputs.skip != 'true'" in body
        assert "needs.nightly.result != 'failure'" in body


def test_1045_queue_windows_is_a_required_dependency_not_pr_only() -> None:
    source = (WORKFLOWS / "pr_ci.yml").read_text()
    windows = source.split("\n  windows:\n", 1)[1].split("\n  arm64:", 1)[0]
    assert "github.event_name != 'merge_group'" not in windows
    required = source.split("\n  required:\n", 1)[1]
    needs = re.search(r"needs: \[([^]]+)\]", required)
    assert needs is not None and "windows" in needs[1]


def test_923_fork_code_cannot_write_certification_outputs() -> None:
    source = (WORKFLOWS / "pr_ci.yml").read_text()
    before_route = source.split("      - name: Route the complete PR diff", 1)[0]
    trusted, verifier = before_route.split(
        "      - name: Read writer certifications using only base code", 1
    )
    # CI trust contract: checkout base code and run it before any head checkout.
    assert "ref: ${{ github.event.pull_request.base.sha }}" in trusted
    assert "path: .certification-base" in trusted
    step = verifier.split("      - uses: actions/checkout@", 1)[0]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in step
    assert "working-directory: .certification-base" in step
    assert "python3 -I scripts/ci_certifications.py" in step
    assert "else\n" in step and "retaining all hosted checks" in step


def top_level_conjuncts(expression: str) -> list[str]:
    """Find mandatory conditions without treating a nested OR as a bypass."""
    expression = expression.strip().removeprefix("${{").removesuffix("}}").strip()
    atoms = []
    start = depth = 0
    quoted = False
    for index, character in enumerate(expression):
        if character == "'":
            quoted = not quoted
        if quoted:
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and expression[index : index + 2] == "||":
            return []
        elif depth == 0 and expression[index : index + 2] == "&&":
            atoms.append(expression[start:index].strip())
            start = index + 2
    atoms.append(expression[start:].strip())
    return atoms


def workflow_concurrency(workflow: Path) -> dict[str, str]:
    """Read workflow-level concurrency without assuming a YAML indent width."""
    lines = workflow.read_text().splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if re.fullmatch(r"concurrency:\s*(?:#.*)?", line)
        ),
        None,
    )
    if start is None:
        return {}
    fields = {}
    for line in lines[start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            break
        key, separator, value = line.strip().partition(":")
        if separator:
            fields[key] = value.strip()
    return fields


def draft_concurrency_group(workflow: Path, *, draft: bool, action: str) -> str:
    """Render the draft-dependent expression; keep identical PR context opaque."""
    group = workflow_concurrency(workflow)["group"]

    def render(expression: re.Match[str]) -> str:
        source = expression[1]
        if "github.event.pull_request.draft" not in source:
            return expression[0]
        source = source.replace("github.event.pull_request.draft", repr(draft))
        source = source.replace("github.event.action", repr(action))
        tree = ast.parse(
            source.replace("&&", " and ").replace("||", " or ").strip(), mode="eval"
        )

        def value(node: ast.AST) -> object:
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.BoolOp):
                result = value(node.values[0])
                for child in node.values[1:]:
                    if (isinstance(node.op, ast.And) and not result) or (
                        isinstance(node.op, ast.Or) and result
                    ):
                        return result
                    result = value(child)
                return result
            if isinstance(node, ast.Compare) and len(node.ops) == 1:
                left, right = value(node.left), value(node.comparators[0])
                if isinstance(node.ops[0], ast.NotEq):
                    return left != right
                if isinstance(node.ops[0], ast.Eq):
                    return left == right
            raise AssertionError(f"Unsupported concurrency expression: {source}")

        return str(value(tree.body))

    return re.sub(r"\$\{\{(.*?)\}\}", render, group)


@pytest.mark.parametrize(
    "workflow", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name
)
def test_936_late_draft_payload_cannot_supersede_readiness(workflow: Path) -> None:
    if all(
        event_path_filters(workflow, event) is None
        for event in ("pull_request", "pull_request_target")
    ):
        pytest.skip("No PR trigger; draft payload cannot supersede readiness")
    concurrency = workflow_concurrency(workflow)
    if concurrency.get("cancel-in-progress", "false") == "false":
        pytest.skip("No cancelling workflow concurrency")
    ready = draft_concurrency_group(workflow, draft=False, action="ready_for_review")
    delayed = draft_concurrency_group(workflow, draft=True, action="synchronize")
    assert delayed != ready, workflow.name
    assert draft_concurrency_group(workflow, draft=False, action="synchronize") == ready
    assert (
        draft_concurrency_group(workflow, draft=True, action="converted_to_draft")
        == ready
    )


@pytest.mark.parametrize("indent", ["  ", "    "])
@pytest.mark.parametrize(
    "trigger",
    [
        "on:\n  pull_request:\n    types: [synchronize, ready_for_review]\n",
        "on:\n    pull_request: {types: [synchronize, ready_for_review]}\n",
        "on: {pull_request: {types: [synchronize, ready_for_review]}}\n",
    ],
)
def test_936_draft_race_regression_accepts_yaml_formatting(
    tmp_path: Path, indent: str, trigger: str
) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        trigger
        + "concurrency:\n"
        + f"{indent}group: pr-${{{{ github.event.pull_request.draft && github.event.action != 'converted_to_draft' && '-draft' || '' }}}}\n"
        + f"{indent}cancel-in-progress: true\n"
        + "jobs:\n  run:\n    steps:\n      - env:\n          group: not-workflow-concurrency\n"
    )
    assert event_path_filters(workflow, "pull_request") is not None
    assert draft_concurrency_group(workflow, draft=True, action="synchronize") != (
        draft_concurrency_group(workflow, draft=False, action="ready_for_review")
    )


@pytest.mark.parametrize(
    "workflow", sorted(WORKFLOWS.glob("*.y*ml")), ids=lambda p: p.name
)
def test_910_pr_workflows_allocate_no_draft_runners_and_wake_on_readiness(
    workflow: Path,
) -> None:
    """Check scheduler gates, including reusable callers and failure summaries."""
    text = workflow.read_text()
    if workflow.name == "copilot_review_collaborators.yml":
        pytest.skip("Owner-approved API-only draft review request (#1045)")
    events = list(
        re.finditer(
            r"^  (pull_request(?:_target)?):\s*\n((?:    .*\n|\n)*)",
            text,
            re.MULTILINE,
        )
    )
    if not events:
        return
    for event in events:
        types = re.search(r"^    types: \[([^]]+)\]", event[2], re.MULTILINE)
        assert types and "ready_for_review" in types[1], workflow.name

    # The indentation is the Actions mapping structure, not step-level `if`s.
    jobs = re.split(r"^  ([\w-]+):\s*\n", text.split("\njobs:\n", 1)[1], flags=re.M)
    for name, body in zip(jobs[1::2], jobs[2::2], strict=True):
        match = re.search(r"^    if: (.*(?:\n      .*)*)", body, re.MULTILINE)
        expression = match[1] if match else ""
        if expression.startswith((">-", "|")):
            expression = " ".join(expression.splitlines()[1:])
        atoms = top_level_conjuncts(expression)
        draft_blocked = any(
            atom
            in {
                "github.event.pull_request.draft != true",
                "github.event.pull_request.draft == false",
                "!github.event.pull_request.draft",
            }
            for atom in atoms
        )
        non_pr_only = any(
            re.fullmatch(
                r"github\.event_name == '(?:workflow_dispatch|workflow_run|schedule|push|merge_group)'",
                atom,
            )
            for atom in atoms
        )
        assert draft_blocked or non_pr_only, (
            f"{workflow.name}: {name} can run on drafts"
        )


@pytest.mark.parametrize("job_name", ["workflow-smoke", "test"])
def test_910_shared_os_suite_skips_drafts_before_expanding_the_runner_matrix(
    job_name: str,
) -> None:
    workflow = (WORKFLOWS / "os_tests.yml").read_text()
    job = re.split(
        r"^  [\w-]+:\s*$",
        workflow.split(f"\n  {job_name}:\n", 1)[1],
        maxsplit=1,
        flags=re.MULTILINE,
    )[0]
    condition = re.search(r"^    if: (.*)", job, re.MULTILINE)
    assert condition
    assert "github.event.pull_request.draft != true" in top_level_conjuncts(
        condition[1]
    )


def test_910_setup_push_trigger_does_not_allocate_runners_for_draft_branches() -> None:
    setup = (WORKFLOWS / "copilot-setup-steps.yml").read_text()
    push = re.search(r"^  push:\n((?:    .*\n)*)", setup, re.MULTILINE)
    assert push
    branches = re.search(r"branches: \[([^]]+)\]", push[1])
    assert branches
    patterns = [pattern.strip().strip("'\"") for pattern in branches[1].split(",")]
    assert any(fnmatchcase("main", pattern) for pattern in patterns)
    assert not any(fnmatchcase("ci/draft-pr", pattern) for pattern in patterns)


@pytest.mark.parametrize("action", ["labeled", "unlabeled"])
def test_954_label_events_cannot_shadow_the_required_suite(action: str) -> None:
    assert "pr_ci.yml" not in triggered_workflows(
        [".github/workflows/pr_ci.yml"], "pull_request", action=action
    )


def test_954_late_skipped_draft_cannot_satisfy_pending_ready_required() -> None:
    workflow = (WORKFLOWS / "pr_ci.yml").read_text()
    required = workflow.split("\n  required:\n", 1)[1]
    name = re.search(r"^    name: (.+)$", required, re.MULTILINE)
    assert name
    condition = re.fullmatch(
        r"\$\{\{ github.event.pull_request.draft && '([^']+)' \|\| '([^']+)' \}\}",
        name[1],
    )
    assert condition, "Draft and ready suites must use distinct merge-check names"
    draft_name, ready_name = condition.groups()
    checks = [
        (ready_name, "pending"),
        (draft_name, "skipped"),
    ]
    # A skipped check is accepted by GitHub; it must not replace real validation.
    matching = [state for check_name, state in checks if check_name == "required"]
    assert matching == ["pending"]
    # GitHub can leave a skipped job's expression unrendered; that is safe too.
    assert name[1] != "required"


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


def test_949_required_waits_for_area_gate_without_a_dependency_cycle() -> None:
    workflow = (WORKFLOWS / "pr_ci.yml").read_text()
    area, required = workflow.split("\n  ready-gate:\n", 1)[1].split(
        "\n  required:\n", 1
    )
    area_needs = re.search(r"^    needs: \[([^]]+)\]", area, re.MULTILINE)
    required_needs = re.search(r"^    needs: \[([^]]+)\]", required, re.MULTILINE)
    assert area_needs and required_needs
    # CI contract: branch protection must wait for the real area verdict.
    assert "ready-gate" in required_needs[1].split(", ")
    assert "required" not in area_needs[1].split(", ")
    assert {"macos", "windows", "arm64", "smoke", "install"} <= set(
        area_needs[1].split(", ")
    )
    assert "EVENT_NAME: ${{ github.event_name }}" in required


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
        (
            "Swatinem/rust-cache@d1527c8f67ef9af357290475d4af4b0fab8f12e0 # v2.9.3",
            "rust-cache",
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


@pytest.mark.parametrize(
    ("workflow", "action"),
    [
        ("gui-build", "rust-cache"),
        ("gui-tauri-installer", "rust-cache"),
        ("linux_aligners", "compiler-cache"),
        ("macos_aligners", "compiler-cache"),
        ("linux_deepvariant", "compiler-cache"),
        ("macos_deepvariant", "compiler-cache"),
        ("windows_tools", "compiler-cache"),
        ("glimpse2", "compiler-cache"),
    ],
)
def test_916_expensive_builds_use_trusted_shared_cache(
    workflow: str, action: str
) -> None:
    text = (policy.ROOT / f".github/workflows/{workflow}.yml").read_text()
    # CI contract: compile jobs cannot bypass the shared trust boundary.
    assert f"uses: ./.github/actions/{action}" in text
    assert "uses: actions/cache" not in text


@pytest.mark.parametrize("action", ["compiler-cache", "rust-cache"])
@pytest.mark.parametrize(
    ("ref", "event", "actor", "expected"),
    [
        ("refs/pull/916/merge", "pull_request", "theontho", False),
        ("refs/pull/916/merge", "pull_request", "external", False),
        ("refs/heads/feature", "push", "theontho", False),
        ("refs/tags/v1.0.0", "push", "theontho", False),
        ("refs/heads/main", "push", "theontho", True),
        ("refs/heads/main", "schedule", "theontho", True),
        ("refs/heads/main", "pull_request_target", "theontho", False),
        ("refs/heads/main", "pull_request", "theontho", False),
        ("refs/heads/ci/916-compiler-cache", "pull_request", "theontho", False),
        ("refs/heads/ci/916-compiler-cache", "push", "theontho", False),
        ("refs/heads/ci/916-compiler-cache", "workflow_dispatch", "external", False),
        ("refs/heads/ci/916-compiler-cache", "workflow_dispatch", "theontho", True),
    ],
)
def test_916_pr_refs_cannot_save_compiler_caches(
    action: str, ref: str, event: str, actor: str, expected: bool
) -> None:
    text = (policy.ROOT / f".github/actions/{action}/action.yml").read_text()
    if action == "rust-cache":
        condition = text.split("save-if:", 1)[1]
    else:
        condition = text.split("- name: Save successful compiler objects", 1)[1]
        condition = condition.split("uses:", 1)[0]
    values = {
        "github.ref": ref,
        "github.event_name": event,
        "github.actor": actor,
        "inputs.phase": "save",
        "inputs.writer": "true",
    }
    for name, value in values.items():
        condition = condition.replace(name, repr(value))
    condition = (
        condition.split("if:", 1)[-1]
        .replace(">-", "")
        .replace("${{", "")
        .replace("}}", "")
    )
    condition = condition.replace("&&", " and ").replace("||", " or ")
    tree = ast.parse(" ".join(condition.split()), mode="eval")

    # CI contract: evaluate the actual writer guard for PR, tag, main and
    # owner dispatch contexts, without executing code from a workflow.
    def matches(node: ast.AST) -> bool | str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BoolOp):
            truth_values = [bool(matches(value)) for value in node.values]
            return (
                all(truth_values) if isinstance(node.op, ast.And) else any(truth_values)
            )
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = matches(node.left), matches(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        raise AssertionError(f"Unreviewed writer condition: {ast.dump(node)}")

    assert matches(tree.body) is expected
    if ref.startswith("refs/pull/"):
        assert re.search(r"measure-916-[a-z]+-v1", text)


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
AREA_PUSH_WORKFLOWS = {
    "install_lab.yml",
    "gui-distribution.yml",
    "gui-ci.yml",
    "gui-dev-setup.yml",
    "macos_aligners.yml",
    "macos_deepvariant.yml",
}


def github_glob_matches(path: str, pattern: str) -> bool:
    """Evaluate the *, ** subset used here, including root files and dotfiles."""
    assert not any(char in pattern for char in "?[]+\\"), (
        f"Unsupported GitHub filter pattern: {pattern!r}"
    )
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


def yaml_flow_value(text: str) -> object:
    """Read flow lists/maps and string scalars used by event filters."""
    tokens = re.findall(
        r""""(?:\\.|[^"\\])*"|'(?:''|[^'])*'|[{}\[\],:]|[^{}\[\],:\s]+""", text
    )
    index = 0

    def parse() -> object:
        nonlocal index
        token = tokens[index]
        index += 1
        if token in ("[", "{"):
            closing = "]" if token == "[" else "}"
            items: list[object] = []
            mapping: dict[str, object] = {}
            while tokens[index] != closing:
                item = parse()
                if token == "{":
                    assert isinstance(item, str) and tokens[index] == ":"
                    index += 1
                    mapping[item] = parse()
                else:
                    items.append(item)
                if tokens[index] == closing:
                    break
                assert tokens[index] == ","
                index += 1
            index += 1
            return mapping if token == "{" else items
        if token.startswith('"'):
            return json.loads(token)
        if token.startswith("'"):
            return token[1:-1].replace("''", "'")
        return None if token in ("null", "~") else token

    result = parse()
    assert index == len(tokens), f"Unsupported YAML flow value: {text!r}"
    return result


def event_path_filters(workflow: Path, event: str) -> dict[str, list[str]] | None:
    """Read block/flow event filters without adding docs-job dependencies."""
    on = re.search(
        r"^(?:on|'on'|\"on\"):(?P<value>[^\n]*)$", workflow.read_text(), re.MULTILINE
    )
    assert on is not None, f"Unsupported event syntax in {workflow.name}"
    filter_keys = {"branches", "branches-ignore", "paths", "paths-ignore", "types"}

    def filters_from(value: object) -> dict[str, list[str]]:
        if value is None:
            return {}
        assert isinstance(value, dict), workflow.name
        filters = {}
        for key in filter_keys & value.keys():
            patterns = value[key]
            assert isinstance(patterns, list) and all(
                isinstance(pattern, str) for pattern in patterns
            ), workflow.name
            filters[key] = cast(list[str], patterns)
        return filters

    inline = on["value"].partition("#")[0].strip()
    if inline:
        events = yaml_flow_value(inline)
        if isinstance(events, str):
            return {} if event == events else None
        if isinstance(events, list):
            return {} if event in events else None
        assert isinstance(events, dict), workflow.name
        return filters_from(events[event]) if event in events else None

    text = workflow.read_text()
    lines = text[on.end() :].splitlines()
    in_event = False
    filters: dict[str, list[str]] = {}
    list_key = None
    event_indent = None
    key_indent = None
    for line in lines:
        content = line.partition("#")[0].rstrip()
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip())
        if indent == 0:
            break
        if event_indent is None:
            event_indent = indent
        if indent == event_indent:
            if in_event:
                break
            name, _, value = content.strip().partition(":")
            in_event = name.strip("'\"") == event
            if in_event and value.strip():
                return filters_from(yaml_flow_value(value.strip()))
            continue
        if not in_event:
            continue
        if list_key is not None and content.strip().startswith("- "):
            assert key_indent is not None and indent >= key_indent, workflow.name
            filters[list_key].append(cast(str, yaml_flow_value(content.strip()[2:])))
            continue
        if key_indent is None:
            key_indent = indent
        if indent == key_indent:
            key, _, value = content.strip().partition(":")
            list_key = key if key in filter_keys else None
            if list_key is not None:
                filters[list_key] = []
                if value.strip():
                    filters.update(
                        filters_from({list_key: yaml_flow_value(value.strip())})
                    )
                    list_key = None
    return filters if in_event else None


def triggered_workflows(
    paths: list[str], event: str = "push", *, action: str = "synchronize"
) -> set[str]:
    selected = set()
    for workflow in (policy.ROOT / ".github/workflows").glob("*.yml"):
        push = event_path_filters(workflow, event)
        if push is None:
            continue
        if event in {"pull_request", "pull_request_target"}:
            types = push.get("types", ["opened", "synchronize", "reopened"])
            if action not in types:
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
