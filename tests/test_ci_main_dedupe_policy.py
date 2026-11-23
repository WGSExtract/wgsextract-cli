"""Main pushes skip only what their passed merge group ran (#1373, merge-queue starvation)."""

import os as os
import re as re
import shutil as shutil
import subprocess as subprocess
import textwrap as textwrap
from pathlib import Path as Path

import pytest as pytest

from tests.test_github_actions_policy import WORKFLOWS


def os_suite_cache_flags(tmp_path: Path, event: str, ref: str, run: str = "") -> str:
    """Run the shared suite's test step with a stand-in pixi; return its flags."""
    source = (WORKFLOWS / "os_tests.yml").read_text()
    step = source.split("      - name: Run Tests\n", 1)[1].split("      - name:", 1)[0]
    script = tmp_path / "run-tests.sh"
    script.write_text(textwrap.dedent(step.split("run: |\n", 1)[1]))
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "pixi").write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS"\n')
    (binary / "pixi").chmod(0o755)
    env = os.environ | {
        "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
        "ARGS": str(tmp_path / "args"),
        "EVENT": event,
        "REF": ref,
        "TEST_SELECTION": "all",
        "TIER": "all",
        "SHARD": "all",
        "OS": "ubuntu",
        "SKIP_INDEPENDENT": "false",
        "MERGE_GROUP_RUN": run,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    subprocess.run(["bash", "-e", str(script)], env=env, check=True)
    return (tmp_path / "args").read_text()


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None, reason="Ubuntu suite shell"
)
@pytest.mark.parametrize(
    ("event", "ref", "run", "reads_queue", "records_queue"),
    [
        ("merge_group", "refs/heads/gh-readonly-queue/main/pr-1", "", False, True),
        ("push", "refs/heads/main", "7", True, False),
        ("push", "refs/heads/main", "", False, False),
        ("schedule", "refs/heads/main", "7", False, False),
        ("workflow_dispatch", "refs/heads/main", "7", False, False),
    ],
)
def test_1373_only_main_pushes_skip_what_their_merge_group_ran(
    tmp_path: Path,
    event: str,
    ref: str,
    run: str,
    reads_queue: bool,
    records_queue: bool,
) -> None:
    flags = os_suite_cache_flags(tmp_path, event, ref, run)
    # contract: the queue shares only files it executed; only a matched main
    # push reads them, and never main's own manifest, so the union is full.
    assert ("merge-group-7=" in flags) is reads_queue
    assert ("--record-executed" in flags) is records_queue
    if event != "merge_group":
        assert "main=out/test-cache/main" not in flags


def test_1373_queue_dedupe_is_limited_to_suites_the_queue_runs() -> None:
    suite = (WORKFLOWS / "os_tests.yml").read_text()
    lookup = suite.split("      - name: Find the merge group", 1)[1].split("- name:")[0]
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
