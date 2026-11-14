import re
from pathlib import Path

import pytest

from scripts import validate_github_actions as policy

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


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


@pytest.mark.parametrize(
    "workflow", sorted(WORKFLOWS.glob("*.y*ml")), ids=lambda p: p.name
)
def test_910_pr_workflows_allocate_no_draft_runners_and_wake_on_readiness(
    workflow: Path,
) -> None:
    """Check scheduler gates, including reusable callers and failure summaries."""
    text = workflow.read_text()
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


def test_910_redrafting_cancels_the_separate_install_lab_label_lane() -> None:
    """Redrafting must enter both ready-run groups without allocating a runner."""
    area = (WORKFLOWS / "pr_ci.yml").read_text()
    cancellation = (WORKFLOWS / "pr_draft_cancel.yml").read_text()
    area_group = re.search(r"^  group: (.*)", area, re.MULTILINE)
    label_group = re.search(r"^  group: (.*)", cancellation, re.MULTILINE)
    assert area_group and label_group
    label_run = re.sub(
        r"\$\{\{ contains\(.*\}\}",
        "label-run-install-lab",
        area_group[1],
    )
    code_run = re.sub(r"\$\{\{ contains\(.*\}\}", "diff", area_group[1])
    assert label_group[1] == label_run
    assert label_group[1] != code_run
    assert "converted_to_draft" in cancellation
    assert "cancel-in-progress: true" in cancellation


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
