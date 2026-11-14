from pathlib import Path

import pytest

from scripts import validate_github_actions as policy


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
