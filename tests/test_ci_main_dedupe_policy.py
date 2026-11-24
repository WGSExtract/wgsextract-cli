"""Main pushes skip only what their passed merge group ran (#1373, merge-queue starvation)."""

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.test_github_actions_policy import WORKFLOWS, matches_workflow_condition


def suite_step(name: str) -> str:
    suite = (WORKFLOWS / "os_tests.yml").read_text()
    return suite.split(f"      - name: {name}\n", 1)[1].split("      - name:", 1)[0]


def test_1373_symbol_gate_cannot_hide_cache_skips_from_main() -> None:
    step = suite_step("Run Tests")
    # contract: the PR selector cannot narrow the queue/main complement.
    assert "github.event_name == 'pull_request' && inputs.tests || 'all'" in step
    pr_gate = step.split(
        'if [ "$OS" = ubuntu ] && [ "$EVENT" = pull_request ]; then', 1
    )
    assert "-m scripts.ci_impact_gate" not in pr_gate[0]
    assert "-m scripts.ci_impact_gate" in pr_gate[1]
    main = step.split('elif [ "$REF" = refs/heads/main ]; then', 1)[1].split(
        "\n          else", 1
    )[0]
    assert 'if [ "$EVENT" = push ] && [ -n "$MERGE_GROUP_RUN" ]; then' in main
    assert "--cache-read main=" not in main
    assert "steps.merge-group.outcome == 'success'" in step


def test_1373_daily_linux_backstop_cannot_reuse_push_coverage() -> None:
    ubuntu = (WORKFLOWS / "ubuntu_ci.yml").read_text()
    assert re.search(r'^  schedule:\n    - cron: "\d+ \d+ \* \* \*"', ubuntu, re.M)
    suite = ubuntu.split("\n  test-ubuntu:\n", 1)[1].split("\n  optional-tools:", 1)[0]
    # contract: latest-main regular pytest bypasses green-push reuse, and its
    # uncached pass refreshes cache while a failure retains replay evidence.
    assert "needs: nightly" not in suite
    shared = (WORKFLOWS / "os_tests.yml").read_text().split("\n  test:\n", 1)[1]
    checkout = shared.split("      - name: Verify native runner architecture", 1)[0]
    assert not re.search(r"^\s+ref:", checkout, re.M)
    main = suite_step("Run Tests").split('elif [ "$REF" = refs/heads/main ]; then', 1)[
        1
    ]
    assert (
        "--cache-record out/test-cache/main/passed.json"
        in main.split("\n          else", 1)[0]
    )
    assert "out/test-cache/main.updated" in suite_step("Save test hashes that passed")
    replay = suite_step("Attribute failed daily Linux tests")
    assert "steps.backstop.outputs.trusted == 'true'" in replay
    assert "steps.regular-pytest.outcome == 'failure'" in replay
    assert "-m scripts.ci_impact_backstop" in replay


def test_1373_queue_dedupe_is_limited_to_suites_the_queue_runs() -> None:
    lookup = suite_step("Find the merge group that already tested this main SHA")
    # Nightly, dispatch and other refs keep the full suite.
    assert (
        "if: inputs.queue-dedupe && github.event_name == 'push' && "
        "github.ref == 'refs/heads/main'" in lookup
    )
    jobs = re.split(r"^  [\w-]+:\n", (WORKFLOWS / "pr_ci.yml").read_text(), flags=re.M)
    for caller in (
        "ubuntu_ci.yml",
        "windows_ci.yml",
        "macos_ci.yml",
        "linux_arm64.yml",
    ):
        job = next(job for job in jobs if f"uses: ./.github/workflows/{caller}" in job)
        queued = "github.event_name != 'merge_group'" not in job
        dedupes = "queue-dedupe: true" in (WORKFLOWS / caller).read_text()
        assert dedupes is queued, caller


@pytest.mark.parametrize(
    ("event", "ref", "optional_only", "trusted"),
    [
        ("schedule", "refs/heads/main", False, True),
        ("workflow_dispatch", "refs/heads/main", False, True),
        ("workflow_dispatch", "refs/heads/main", True, False),
        ("workflow_dispatch", "refs/heads/topic", False, False),
        ("workflow_dispatch", "refs/tags/main", False, False),
        ("push", "refs/heads/main", False, False),
        ("pull_request", "refs/pull/1464/merge", False, False),
        ("merge_group", "refs/heads/gh-readonly-queue/main/pr-1464", False, False),
    ],
)
def test_1464_only_full_ubuntu_main_runs_publish_observations(
    event: str, ref: str, optional_only: bool, trusted: bool
) -> None:
    ubuntu = (WORKFLOWS / "ubuntu_ci.yml").read_text()
    gate = re.search(
        r"main-backstop-candidate: &main-backstop-candidate \$\{\{ (.+) \}\}", ubuntu
    )
    assert gate is not None
    inputs: dict[str, str | bool] = {
        "github.event_name": event,
        "github.ref": ref,
        "inputs.optional-tools-only": optional_only,
    }
    assert matches_workflow_condition(gate[1], inputs) is trusted

    for name, outcome, step in [
        ("Export Linux test observations", "success", "regular-pytest"),
        ("Save main's Linux observation map", "success", "impact-map"),
        ("Attribute failed daily Linux tests", "failure", "regular-pytest"),
        ("Upload Linux selector miss receipts", "failure", "regular-pytest"),
    ]:
        condition = re.search(r"if: \$\{\{ (.+) \}\}", suite_step(name))
        assert condition is not None
        for actual in ("success", "failure", "skipped"):
            assert matches_workflow_condition(
                condition[1],
                {
                    "cancelled()": False,
                    "steps.backstop.outputs.trusted": "true" if trusted else "",
                    f"steps.{step}.outcome": actual,
                },
            ) is (trusted and actual == outcome), name


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None, reason="Ubuntu runner shell"
)
@pytest.mark.parametrize(
    ("ref", "trusted"),
    [("refs/heads/main", True), ("refs/heads/Main", False), ("refs/tags/main", False)],
)
def test_1464_backstop_requires_the_exact_main_ref(
    tmp_path: Path, ref: str, trusted: bool
) -> None:
    output = tmp_path / "github-output"
    step = suite_step("Verify the full backstop ref").split("        run: |\n", 1)[1]
    script = textwrap.dedent(step.split("\n      - uses:", 1)[0])
    subprocess.run(
        ["bash", "-ec", script],
        env=os.environ | {"GITHUB_REF": ref, "GITHUB_OUTPUT": output.as_posix()},
        check=True,
    )
    assert (output.read_text().strip() if output.exists() else "") == (
        "trusted=true" if trusted else ""
    )


@pytest.mark.parametrize(
    ("event", "branch", "repository", "conclusion", "eligible"),
    [
        ("schedule", "main", "owner/repo", "failure", True),
        ("workflow_dispatch", "main", "owner/repo", "failure", True),
        ("workflow_dispatch", "topic", "owner/repo", "failure", False),
        ("workflow_dispatch", "main", "fork/repo", "failure", False),
        ("workflow_dispatch", "main", "owner/repo", "success", False),
        ("pull_request", "main", "owner/repo", "failure", False),
    ],
)
def test_1464_miss_publisher_only_considers_failed_main_backstops(
    event: str, branch: str, repository: str, conclusion: str, eligible: bool
) -> None:
    source = (WORKFLOWS / "selector_misses.yml").read_text()
    condition = source.split("    if: >-\n", 1)[1].split("    runs-on:", 1)[0]
    contexts: dict[str, str | bool] = {
        "github.event.workflow_run.event": event,
        "github.event.workflow_run.head_branch": branch,
        "github.event.workflow_run.head_repository.full_name": repository,
        "github.event.workflow_run.conclusion": conclusion,
        "github.repository": "owner/repo",
    }
    assert matches_workflow_condition(condition, contexts) is eligible
    verify, download = source.split("- name: Download confirmed omission receipts")
    assert "ref: ${{ github.sha }}" in verify
    assert 'git rev-list --first-parent HEAD | grep -Fx "$RUN_SHA"' in verify
    assert "continue-on-error" not in verify
    assert "run-id: ${{ github.event.workflow_run.id }}" in download
