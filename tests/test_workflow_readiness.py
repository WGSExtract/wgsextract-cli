"""Focused workflow readiness regressions."""

import ast
import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from scripts import validate_github_actions as policy
from tests.test_github_actions_policy import (
    WORKFLOWS as WORKFLOWS,
)
from tests.test_github_actions_policy import (
    event_path_filters as event_path_filters,
)
from tests.test_github_actions_policy import (
    matches_workflow_condition as matches_workflow_condition,
)
from tests.test_github_actions_policy import (
    top_level_conjuncts as top_level_conjuncts,
)
from tests.test_github_actions_policy import (
    triggered_workflows as triggered_workflows,
)


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
    fields: dict[str, str] = {}
    field_indent = None
    folded_key = None
    for line in lines[start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if not indent:
            break
        if field_indent is None:
            field_indent = indent
        if indent > field_indent and folded_key is not None:
            fields[folded_key] = f"{fields[folded_key]} {line.strip()}".lstrip()
            continue
        key, separator, value = line.strip().partition(":")
        if separator:
            folded_key = key if value.strip() == ">-" else None
            fields[key] = "" if folded_key else value.strip()
    return fields


def draft_concurrency_group(workflow: Path, *, draft: bool, action: str) -> str:
    """Render the draft-dependent expression; keep identical PR context opaque."""
    return render_concurrency_group(
        workflow_concurrency(workflow)["group"],
        {"github.event.pull_request.draft": draft, "github.event.action": action},
    )


def render_concurrency_group(
    group: str, values: dict[str, str | bool | tuple[str, ...]]
) -> str:
    def render(expression: re.Match[str]) -> str:
        source = expression[1]
        if not any(name in source for name in values):
            return expression[0]
        for name, context_value in values.items():
            source = source.replace(name, repr(context_value))
        tree = ast.parse(
            re.sub(
                r"!(?!=)", " not ", source.replace("&&", " and ").replace("||", " or ")
            ).strip(),
            mode="eval",
        )

        def value(node: ast.AST) -> object:
            if isinstance(node, ast.Constant):
                return node.value
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "contains"
                and len(node.args) == 2
                and not node.keywords
            ):
                labels, label = node.args
                if isinstance(labels, ast.Tuple):
                    return str(value(label)).casefold() in {
                        str(value(item)).casefold() for item in labels.elts
                    }
                return str(value(label)).casefold() in str(value(labels)).casefold()
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                return not value(node.operand)
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
    ("moves", "status", "certified"),
    [
        ("[]", "true", "true"),
        ('["wgsextract_cli.old"]', "true", "false"),
        ("[]", "false", "false"),
    ],
)
def test_1492_windows_shard_planning_uses_effective_certification(
    moves: str, status: str, certified: str
) -> None:
    workflow = (WORKFLOWS / "pr_ci.yml").read_text()
    gate = re.search(r"^\s+windows_x64: &(\S+) (.+)$", workflow, re.M)
    assert gate is not None
    anchor, expression = gate.groups()
    planner = workflow.split("id: shards", 1)[1].split(
        "- name: Set up the locked Python", 1
    )[0]
    assert f"WINDOWS_CERTIFIED: *{anchor}" in planner
    assert '[ "$WINDOWS_CERTIFIED" != true ]' in planner
    assert (
        render_concurrency_group(
            expression,
            {
                "steps.diff.outputs.move_modules": moves,
                "steps.cert.outputs.windows_x64": status,
            },
        )
        == certified
    )


def test_1488_noop_pr_runs_do_not_queue_behind_failure_observers() -> None:
    concurrency = workflow_concurrency(WORKFLOWS / "rerun_lost_runners.yml")

    def group(event: str, run_id: str, *labels: str) -> str:
        return render_concurrency_group(
            concurrency["group"],
            {
                "github.event_name": event,
                "github.run_id": run_id,
                "github.event.pull_request.labels.*.name": labels,
            },
        )

    # contract: serialize shared state updates, not PR runs without ci-red-main.
    observations = group("workflow_run", "1")
    assert group("workflow_run", "2") == observations
    assert group("pull_request_target", "3", "bug", "ci-red-main") == observations
    weekly = group("schedule", "4")
    assert group("schedule", "5") == weekly
    no_op_runs = {
        group("pull_request_target", "6"),
        group("pull_request_target", "7"),
        group("pull_request_target", "8", "ci-red-main-related"),
    }
    assert len(no_op_runs | {observations, weekly}) == 5
    assert concurrency["queue"] == "max"
    assert concurrency["cancel-in-progress"] == "false"


def test_1237_gui_upload_requires_the_build_matrix_signed_updater_targets() -> None:
    build = (WORKFLOWS / "gui-build.yml").read_text()
    distribution = (WORKFLOWS / "gui-distribution.yml").read_text()
    validation, upload = distribution.split("\n  assets:\n", 1)[1].split(
        "\n  publish:\n", 1
    )

    # CI contract: require the same target list that drove the installers,
    # and block uploads (including replacements) when feed validation fails.
    assert "artifact_name: ${{ fromJSON(inputs.targets) }}" in build
    assert "targets: ${{ inputs.targets }}" in build
    assert "value: ${{ jobs.tauri.outputs.targets }}" in build
    assert "needs: tauri" in validation
    assert "inputs.tag != ''" in validation.split("steps:", 1)[0]
    assert "ref: ${{ github.workflow_sha }}" in validation
    assert "EXPECTED_TARGETS: ${{ needs.tauri.outputs.targets }}" in validation
    assert '--expected-targets "$EXPECTED_TARGETS"' in validation
    assert "--allow-unsigned" not in distribution
    assert "continue-on-error" not in validation
    assert "gh release upload" in upload and "gh release upload" not in validation
    assert "Only Release preflight may supply assets for a draft." in upload


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


def test_1492_duration_matrix_leaves_queue_and_arm64_single_job() -> None:
    suite = (WORKFLOWS / "os_tests.yml").read_text()
    fallback = re.search(r"fromJSON\(inputs\.matrix \|\| '(.+)'\)", suite)
    assert fallback is not None
    assert [entry["label"] for entry in json.loads(fallback[1])] == ["all"]
    arm = (WORKFLOWS / "linux_arm64.yml").read_text()
    matrix = re.search(r"^      matrix: '(.+)'$", arm, re.MULTILINE)
    assert matrix is not None
    assert [entry["label"] for entry in json.loads(matrix[1])] == ["native"]
    plan = (WORKFLOWS / "pr_ci.yml").read_text().split("id: shards", 1)[1]
    assert "if: github.event_name == 'pull_request'" in plan.split("env:", 1)[0]
    assert "writer: ${{ strategy.job-index == 0 }}" in suite
    assert 'cache-job "os=$OS tier=all shard=all ' in suite


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
    ],
)
def test_daily_backstop_runs_even_when_the_latest_main_sha_is_unchanged(
    workflow: str,
) -> None:
    path = WORKFLOWS / workflow
    source = path.read_text()
    assert re.search(r'^  schedule:\n    - cron: "\d+ \d+ \* \* \*"', source, re.M)
    assert event_path_filters(path, "workflow_dispatch") is not None
    assert event_path_filters(path, "push") is None
    assert "outputs.skip" not in source
    name = source.splitlines()[0].removeprefix("name: ")
    observer = (WORKFLOWS / "rerun_lost_runners.yml").read_text()
    assert f"      - {name}\n" in observer


def test_1464_ubuntu_dispatch_reaches_the_full_backstop_through_reusable_calls() -> (
    None
):
    ubuntu = (WORKFLOWS / "ubuntu_ci.yml").read_text()
    backstop = ubuntu.split("\n  test-ubuntu:\n", 1)[1].split(
        "\n  test-ubuntu-balanced:", 1
    )[0]
    # A slow, failed or skipped guard cannot prevent a trusted full pytest run.
    assert "needs.nightly" not in backstop and "needs: nightly" not in backstop
    assert "main-backstop-candidate: " in backstop
    suite = (WORKFLOWS / "os_tests.yml").read_text()
    assert (
        "timeout-minutes: ${{ inputs.main-backstop-candidate && 80 || inputs.timeout-minutes }}"
        in suite
    )
    assert "TRUSTED_FULL_MAIN: ${{ steps.backstop.outputs.trusted }}" in suite
    assert (
        'if [ "$TRUSTED_FULL_MAIN" = true ]; then\n            args+=(--cov-context=test'
        in suite
    )
    for name in ("macos_ci.yml", "windows_ci.yml", "linux_arm64.yml", "cli_smoke.yml"):
        assert "main-backstop-candidate:" not in (WORKFLOWS / name).read_text()


def test_1452_only_trusted_full_ubuntu_coverage_can_seed_the_line_cache() -> None:
    suite = (WORKFLOWS / "os_tests.yml").read_text()
    steps = dict(
        re.findall(
            r"^      - name: ([^\n]+)\n(.*?)(?=^      - name: |\Z)", suite, re.M | re.S
        )
    )
    export = steps["Export advisory Linux line observations"]
    for name in (
        "Export advisory Linux line observations",
        "Save main's advisory Linux line map",
        "Archive the advisory Linux line map",
    ):
        assert "steps.backstop.outputs.trusted == 'true'" in steps[name]
        assert "continue-on-error: true" in steps[name]
    assert "steps.impact-map.outcome == 'success'" in export
    assert "--coverage-file out/.coverage" in export
    assert "--line-map" not in steps["Run Tests"]
    assert "--line-map" in steps["Compare shadow selection with regular Linux pytest"]
    assert "--line-map" in steps["Publish one Linux selection receipt from every shard"]
    assert (
        "overwrite: true"
        in steps["Upload advisory Linux selection and outcome receipt"]
    )
    assert suite.index("Upload Linux selector miss receipts") < suite.index(
        "Score advisory Linux line recall"
    )


def test_1045_move_import_collection_cannot_be_skipped_by_writer_certificates() -> None:
    source = (WORKFLOWS / "pr_ci.yml").read_text()
    for platform in ("linux_x64", "linux_arm64", "macos_arm64", "windows_x64"):
        output = re.search(rf"^      {platform}: (.+)$", source, re.MULTILINE)
        assert output is not None
        assert "steps.diff.outputs.move_modules == '[]' &&" in output[1]
        assert (
            render_concurrency_group(
                output[1],
                {
                    "steps.diff.outputs.move_modules": '["wgsextract_cli.old"]',
                    "steps.cert.outputs.generic": "true",
                    f"steps.cert.outputs.{platform}": "true",
                },
            )
            .split()[-1]
            .lower()
            == "false"
        )


def test_1045_bundle_compatibility_keeps_affected_parser_dependencies() -> None:
    source = (WORKFLOWS / "os_tests.yml").read_text()
    bundle = source.split(
        "      - name: Check the app's bundle commands parse with this CLI", 1
    )[1]
    condition = bundle.splitlines()[1]
    assert "inputs.setup-checks ||" in condition
    assert "contains(inputs.tests, 'tests/test_cli_parser.py')" in condition


def test_1045_queue_smoke_is_required_instead_of_the_pr_windows_suite() -> None:
    source = (WORKFLOWS / "pr_ci.yml").read_text()
    windows = source.split("\n  windows:\n", 1)[1].split("\n  arm64:", 1)[0]
    assert "github.event_name == 'pull_request'" in windows
    required = source.split("\n  required:\n", 1)[1]
    needs = re.search(r"needs: \[([^]]+)\]", required)
    assert needs is not None and "queue-smoke" in needs[1]


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
    if workflow.name == "priority_collaborator_prs.yml":
        # Label drafts without allowing this exception to run PR code or gain
        # broader permissions. Its only step is the reviewed API-only labeler.
        assert event_path_filters(workflow, "pull_request_target") is not None
        assert event_path_filters(workflow, "pull_request") is None
        assert not re.search(r"^\s*(?:-\s*)?uses:", text, re.MULTILINE)
        assert re.findall(r"^ *permissions:.*$", text, re.MULTILINE) == [
            "permissions: {}",
            "    permissions:",
        ]
        assert "    permissions:\n      pull-requests: write\n    steps:" in text
        assert len(re.findall(r"^\s*(?:-\s*)?run:", text, re.MULTILINE)) == 1
        return
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


@pytest.mark.parametrize("job_name", ["workflow-smoke", "test", "selection-shadow"])
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
    assert {"macos", "windows", "arm64"} <= set(area_needs[1].split(", "))
    assert not {"smoke", "install"} & set(area_needs[1].split(", "))
    assert "EVENT_NAME: ${{ github.event_name }}" in required
