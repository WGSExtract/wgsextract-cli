"""Main pushes skip only what their passed merge group ran (#1373, merge-queue starvation)."""

import re as re

from tests.test_github_actions_policy import WORKFLOWS


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
    assert "github.event_name == 'schedule' || needs.nightly.result" in suite
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
    assert "github.event_name == 'schedule'" in replay
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
