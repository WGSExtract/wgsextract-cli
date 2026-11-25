import ast as ast
import json as json
import os as os
import re as re
import shlex as shlex
import shutil as shutil
import subprocess as subprocess
import sys as sys
import textwrap as textwrap
from fnmatch import fnmatchcase as fnmatchcase
from pathlib import Path as Path
from typing import cast as cast

import pytest as pytest

from scripts import validate_github_actions as policy
from scripts.check_remote_resources import live_test_targets as live_test_targets
from scripts.ci_cancel_stale_queue import main as cancel_stale_main
from scripts.ci_changes import routes as routes
from scripts.ci_codeql_scope import LANGUAGES as LANGUAGES
from scripts.ci_github import GitHub as GitHub
from scripts.ci_optional_tools import main as optional_main
from scripts.ci_optional_tools import require_executed as require_executed


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None, reason="Ubuntu publisher shell"
)
def test_1046_failed_publisher_is_not_hidden_by_tee(tmp_path: Path) -> None:
    root = WORKFLOWS.parents[1]
    source = (WORKFLOWS / "mirror_download_resources.yml").read_text()
    step = source.split("- name: Stream, hash and publish one resource\n", 1)[1].split(
        "- name:", 1
    )[0]
    body = textwrap.dedent(step.split("run: |\n", 1)[1])
    script = tmp_path / "publish-step.sh"
    script.write_text(body)
    binary = tmp_path / "bin"
    binary.mkdir()
    pixi = binary / "pixi"
    pixi.write_text(f'#!/bin/sh\nshift 2\nexec {shlex.quote(sys.executable)} "$@"\n')
    pixi.chmod(0o755)
    (tmp_path / "scripts").symlink_to(root / "scripts", target_is_directory=True)
    env = os.environ | {
        "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(root / "src"),
        "RESOURCE": "spliceai-hg38",
        "TAG": "download-resources-test",
    }
    # Match Actions: explicit bash adds pipefail; its default bash only adds -e.
    flags = (
        ["-o", "pipefail"] if re.search(r"^\s+shell:\s+bash\s*$", step, re.M) else []
    )
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", *flags, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "upstream-only" in (tmp_path / "out/mirror/publish.log").read_text()


@pytest.mark.parametrize(
    ("paths", "spliceai", "gene_lists"),
    [
        (["src/wgsextract_cli/core/annotation_resources.py"], True, False),
        (["src/wgsextract_cli/core/gene_lists.py"], False, True),
        (["src/wgsextract_cli/assets/report/gene_lists.json"], False, True),
        (
            [
                "src/wgsextract_cli/core/annotation_resources.py",
                "src/wgsextract_cli/assets/report/gene_lists.json",
            ],
            True,
            True,
        ),
        (["src/wgsextract_cli/core/constants.py"], False, False),
    ],
)
def test_1046_changed_resource_paths_retain_live_pr_behavior_checks(
    paths: list[str], spliceai: bool, gene_lists: bool
) -> None:
    targets = live_test_targets(paths)
    assert ("tests/test_remote_resources.py" in targets) is spliceai
    assert (
        any("test_built_in_gene_list_downloads" in target for target in targets)
        is gene_lists
    )
    source = (WORKFLOWS / "remote_resources.yml").read_text()
    step = source.split(
        "- name: Check changed live SpliceAI and gene-list behavior\n", 1
    )[1]
    step = step.split("- name:", 1)[0]
    # CI contract: changed-URL filtering must not remove behavioral PR tests.
    assert "if: github.event_name == 'pull_request'" in step
    assert '--live-tests-base "$BASE_SHA"' in step


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


@pytest.mark.parametrize("result", ["skipped", "failure", "error", "empty"])
def test_1199_optional_ci_rejects_skipped_or_empty_results(
    tmp_path: Path, result: str
) -> None:
    report = tmp_path / "result.xml"
    report.write_text(
        "<testsuites><testsuite>"
        + (
            f'<testcase name="contamination"><{result}/></testcase>'
            if result != "empty"
            else ""
        )
        + "</testsuite></testsuites>"
    )
    with pytest.raises(ValueError, match="Optional-tool tests"):
        require_executed(report)
    report.write_text(
        '<testsuites><testsuite><testcase name="contamination"/></testsuite></testsuites>'
    )
    require_executed(report)


def test_1199_optional_ci_is_cached_linux_only_and_required() -> None:
    source = (WORKFLOWS / "optional_tools.yml").read_text()
    assert "runs-on: ubuntu-latest" in source
    assert "scope: optional-tools" in source and 'writer: "true"' in source
    assert (
        "environments: dev verifybamid2 freebayes longtr modkit expansionhunter"
        in source
    )
    assert "scripts/ci_optional_tools.py" in source
    pr = (WORKFLOWS / "pr_ci.yml").read_text()
    assert "needs.changes.outputs.optional_tests != '[]'" in pr
    optional_pr = pr.split("\n  optional-tools:\n", 1)[1].split("\n  ubuntu:", 1)[0]
    assert "github.event_name == 'pull_request'" in optional_pr
    assert "needs.changes.outputs.test_certified != 'true'" in optional_pr
    for name in ("ready-gate", "required"):
        job = pr.split(f"\n  {name}:\n", 1)[1]
        needs = re.search(r"^\s+needs: \[(.*)\]$", job, re.M)
        assert needs is not None and "optional-tools" in needs[1]
    ubuntu = (WORKFLOWS / "ubuntu_ci.yml").read_text()
    optional = ubuntu.split("\n  optional-tools:\n", 1)[1].split("\n  imputation:", 1)[
        0
    ]
    assert "github.event_name != 'pull_request'" in optional
    assert "platform-certified" not in optional
    assert "optional-tools-only:" in ubuntu
    assert (
        "default: false"
        in ubuntu.split("optional-tools-only:", 1)[1].split("permissions:", 1)[0]
    )
    for job_name in ("lint", "test-ubuntu", "imputation", "ont-hg002"):
        job = ubuntu.split(f"\n  {job_name}:\n", 1)[1]
        condition = re.search(r"^\s+if: (.*)$", job, re.M)
        assert condition is not None
        assert "!inputs.optional-tools-only" in top_level_conjuncts(condition[1])
    for workflow in ("macos_ci.yml", "windows_ci.yml", "linux_arm64.yml"):
        assert "optional_tools.yml" not in (WORKFLOWS / workflow).read_text()


def test_queue_smoke_cannot_invoke_the_hosted_pr_os_suites() -> None:
    pr = (WORKFLOWS / "pr_ci.yml").read_text()
    for lane in ("windows", "macos", "arm64"):
        condition = pr.split(f"\n  {lane}:\n", 1)[1].splitlines()[1]
        assert "'pull_request'" in condition or "!= 'merge_group'" in condition
    queue = pr.split("\n  queue-smoke:\n", 1)[1].split("\n  #", 1)[0]
    assert "fromJSON(needs.changes.outputs.queue_runners)" in queue
    assert "--collect-only" in queue
    assert "timeout-minutes: 10" in queue
    ubuntu = (WORKFLOWS / "ubuntu_ci.yml").read_text()
    lint = ubuntu.split("\n  lint:\n", 1)[1].split("\n  test-ubuntu:\n", 1)[0]
    assert "mypy" in lint and "certified" not in lint


def test_1199_missing_optional_tool_fails_before_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tool availability is the OS boundary; missing binaries must not become
    # successful pytest invocations whose integration fixture was skipped.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "wgsextract_cli.core.dependencies.get_tool_path", lambda tool: None
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["optional-tools", "--selection", '["tests/test_contamination.py"]'],
    )
    with pytest.raises(
        ValueError, match="Required optional CI tool missing: verifybamid2"
    ):
        optional_main()
    assert not list((tmp_path / "out/optional-tools").glob("*.xml"))


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
    values: dict[str, str | bool] = {
        "github.ref": ref,
        "github.event_name": event,
        "github.actor": actor,
        "inputs.phase": "save",
        "inputs.writer": "true",
    }
    condition = (
        condition.split("if:", 1)[-1]
        .replace(">-", "")
        .replace("${{", "")
        .replace("}}", "")
    )
    assert matches_workflow_condition(condition, values) is expected
    if ref.startswith("refs/pull/"):
        assert re.search(r"measure-916-[a-z]+-v1", text)


def matches_workflow_condition(condition: str, values: dict[str, str | bool]) -> bool:
    for name, value in values.items():
        condition = condition.replace(name, repr(value))
    condition = condition.replace("&&", " and ").replace("||", " or ")
    condition = re.sub(r"!(?!=)", " not ", condition)
    tree = ast.parse(" ".join(condition.split()), mode="eval")

    # Evaluate only the boolean/comparison subset, never execute workflow code.
    def matches(node: ast.AST) -> bool | str:
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not matches(node.operand)
        if isinstance(node, ast.BoolOp):
            truth_values = [bool(matches(value)) for value in node.values]
            return (
                all(truth_values) if isinstance(node.op, ast.And) else any(truth_values)
            )
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = matches(node.left), matches(node.comparators[0])
            if isinstance(left, str) and isinstance(right, str):
                left, right = left.lower(), right.lower()
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        raise AssertionError(f"Unreviewed workflow condition: {ast.dump(node)}")

    return bool(matches(tree.body))


MAIN_PUSH_WORKFLOWS = {
    "gitleaks.yml",
    "codeql.yml",
    "auto_merge_watch.yml",
    "release.yml",
    "pages.yml",
    "copilot-setup-steps.yml",
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
    filter_keys = {
        "branches",
        "branches-ignore",
        "paths",
        "paths-ignore",
        "tags",
        "tags-ignore",
        "types",
    }

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
        if event == "push" and tags_only(push):
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


def tags_only(filters: dict[str, list[str]]) -> bool:
    return bool(
        {"tags", "tags-ignore"} & filters.keys()
        and not {"branches", "branches-ignore"} & filters.keys()
    )


def test_main_branch_push_allowlist_is_independent_of_changed_paths() -> None:
    def allows_main(workflow: Path) -> bool:
        filters = event_path_filters(workflow, "push")
        if filters is None or tags_only(filters):
            return False
        return github_paths_select(["main"], filters.get("branches", ["**"])) and not (
            github_paths_select(["main"], filters.get("branches-ignore", []))
        )

    assert {
        workflow.name for workflow in WORKFLOWS.glob("*.yml") if allows_main(workflow)
    } == MAIN_PUSH_WORKFLOWS


@pytest.mark.parametrize(
    ("action", "relabels"),
    [
        ("opened", True),
        ("reopened", True),
        ("ready_for_review", True),
        ("converted_to_draft", True),
        ("synchronize", False),
        ("unlabeled", False),
    ],
)
def test_collaborator_priority_is_restored_only_on_pr_lifecycle_events(
    action: str, relabels: bool
) -> None:
    workflows = triggered_workflows([], "pull_request_target", action=action)
    assert ("priority_collaborator_prs.yml" in workflows) is relabels


QUEUE = "gh-readonly-queue/main/pr-"


class QueueServer(GitHub):
    """Merge-queue API boundary: run status changes are the observable result."""

    def __init__(self, refs: list[str], runs: dict[int, str]) -> None:
        super().__init__("WGSExtract/wgsextract-cli")
        self.refs = refs
        self.runs = {
            run: {"branch": branch, "status": "in_progress"}
            for run, branch in runs.items()
        }
        self.fail: str | None = None

    def request(self, method: str, path: str, data: object = None) -> object:
        path = path.removeprefix(f"repos/{self.repository}/")
        if self.fail and path.startswith(self.fail):
            raise RuntimeError("HTTP 502")
        if path.startswith("git/matching-refs/heads/gh-readonly-queue/main"):
            return [{"ref": f"refs/heads/{ref}"} for ref in self.refs]
        if path.startswith("actions/workflows/pr_ci.yml/runs"):
            status = re.search(r"status=(\w+)", path)
            assert status is not None and "event=merge_group" in path
            return {
                "workflow_runs": [
                    {"id": run, "head_branch": item["branch"]}
                    for run, item in self.runs.items()
                    if item["status"] == status[1]
                ]
            }
        cancel = re.fullmatch(r"actions/runs/(\d+)/cancel", path)
        assert cancel is not None and method == "POST"
        self.runs[int(cancel[1])]["status"] = "cancelled"
        return None

    def run_cleanup(self, own: str) -> int:
        return cancel_stale_main(
            [
                "--repository",
                self.repository,
                "--workflow",
                "pr_ci.yml",
                "--base",
                "refs/heads/main",
                "--run-id",
                "1",
                "--ref",
                f"refs/heads/{own}",
            ],
            self,
        )

    def status(self) -> dict[int, str]:
        return {run: item["status"] for run, item in self.runs.items()}


def test_1373_only_runs_of_deleted_queue_refs_are_cancelled() -> None:
    live, stale = f"{QUEUE}2-b", f"{QUEUE}1-a"
    # Run 1 is this run; run 4 is not a queue run.
    server = QueueServer(
        [f"{QUEUE}3-c", live], {1: f"{QUEUE}3-c", 2: live, 3: stale, 4: "main"}
    )
    assert server.run_cleanup(f"{QUEUE}3-c") == 0
    assert server.status() == {
        1: "in_progress",
        2: "in_progress",
        3: "cancelled",
        4: "in_progress",
    }


@pytest.mark.parametrize(
    ("refs", "fail"),
    [
        ([], None),  # this run's own ref is missing: the snapshot is not trusted
        ([f"{QUEUE}3-c"] + [f"{QUEUE}{n}" for n in range(99)], None),  # truncated?
        ([f"{QUEUE}3-c"], "git/"),
        ([f"{QUEUE}3-c"], "actions/workflows/"),
    ],
)
def test_1373_untrusted_or_failed_lookups_cancel_nothing_and_never_fail(
    refs: list[str], fail: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    server = QueueServer(refs, {1: f"{QUEUE}3-c", 3: f"{QUEUE}1-a"})
    server.fail = fail
    assert server.run_cleanup(f"{QUEUE}3-c") == 0
    assert server.status()[3] == "in_progress"
    assert "::warning::" in capsys.readouterr().out


def test_1373_failed_cancel_is_a_warning_not_a_failed_merge_group(
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = QueueServer([f"{QUEUE}3-c"], {1: f"{QUEUE}3-c", 3: f"{QUEUE}1-a"})
    server.fail = "actions/runs/"
    assert server.run_cleanup(f"{QUEUE}3-c") == 0
    assert "::warning::Could not cancel stale run 3" in capsys.readouterr().out


def test_1373_queue_cleanup_has_write_access_alone_and_cannot_gate_required() -> None:
    source = (WORKFLOWS / "pr_ci.yml").read_text()
    jobs = re.split(r"\n  (?=[a-z][\w-]*:\n)", source.split("\njobs:\n", 1)[1])
    writers = [job.split(":", 1)[0] for job in jobs if "actions: write" in job]
    assert writers == ["cancel-stale-queue"]
    cleanup = next(job for job in jobs if job.startswith("cancel-stale-queue:"))
    assert "if: github.event_name == 'merge_group'" in cleanup
    assert "continue-on-error: true" in cleanup
    # contract: the actions: write token never runs queued (unmerged) PR code.
    assert "ref: ${{ github.event.merge_group.base_sha }}" in cleanup
    for job in jobs:
        needs = re.search(r"^    needs: (.+)$", job, re.M)
        assert needs is None or "cancel-stale-queue" not in needs[1]


def test_1545_area_required_cannot_release_dependent_runner_jobs() -> None:
    source = (WORKFLOWS / "pr_ci.yml").read_text()
    needs = re.findall(r"^    needs:\s*([^\n]*(?:\n      [^\n]*)*)", source, re.M)
    assert all(not re.search(r"(?<![\w-])required(?![\w-])", entry) for entry in needs)
