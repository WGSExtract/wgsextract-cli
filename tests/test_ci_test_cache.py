"""#915 (test result cache): skip only test files whose exact inputs passed."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.ci_merge_group_passes import fetch
from scripts.ci_run_tests import apply_cache, record_passes
from scripts.ci_select_tests import select_tests
from scripts.ci_test_cache import Fingerprint, Manifest, fingerprint
from scripts.ci_test_dependencies import ROOT

FILES = {
    "pixi.lock": "version: 1",
    "src/wgsextract_cli/__init__.py": "",
    "src/wgsextract_cli/core/__init__.py": "",
    "src/wgsextract_cli/core/alpha.py": "VALUE = 1",
    "src/wgsextract_cli/core/beta.py": "VALUE = 2",
    "reference/input.csv": "value",
    "native/README.txt": "notes",
    "gui/assets/logo.txt": "logo",
    "tests/test_logo.py": 'from pathlib import Path\nPath("gui/assets/logo.txt")',
    "tests/test_alpha.py": "from wgsextract_cli.core import alpha",
    "tests/test_beta.py": "from wgsextract_cli.core import beta",
    "tests/test_reader.py": 'from pathlib import Path\nPath("reference/input.csv")',
}


def checkout(root: Path, files: Mapping[str, str | None]) -> Path:
    """Write (or, for None, delete) files and stage them as a git checkout."""
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = root / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def dependency_hashes(job: str, *, repository: Path) -> dict[str, str]:
    return fingerprint(job, repository=repository).hashes


def changed_hashes(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {test for test in after if before.get(test) != after[test]}


def reusable(before: Fingerprint, after: Fingerprint) -> set[str]:
    """Tests whose pass at ``before`` the cache would reuse at ``after``."""
    manifest = Manifest()
    manifest.record(set(before.hashes.values()), before.names, NOW)
    return {
        test
        for test, digest in after.hashes.items()
        if manifest.passed_with(digest, after.names)
    }


def test_915_hash_follows_each_test_files_imports_and_inputs(tmp_path: Path) -> None:
    before = dependency_hashes("job", repository=checkout(tmp_path / "before", FILES))
    cases = {
        "src/wgsextract_cli/core/alpha.py": {"tests/test_alpha.py"},
        "reference/input.csv": {"tests/test_reader.py"},
        "pixi.lock": set(before),
    }
    for path, expected in cases.items():
        root = checkout(tmp_path / path.replace("/", "_"), FILES | {path: "changed"})
        after = dependency_hashes("job", repository=root)
        assert changed_hashes(before, after) == expected, path
    other_job = dependency_hashes("other-job", repository=tmp_path / "before")
    assert changed_hashes(before, other_job) == set(before)


def test_915_unchanged_hash_never_hides_a_test_the_selector_would_run(
    tmp_path: Path,
) -> None:
    """Never less safe than #914's selection, for edits and deletions alike."""
    edits: list[dict[str, str | None]] = [
        {"src/wgsextract_cli/core/beta.py": "VALUE = 3"},
        {"src/wgsextract_cli/core/beta.py": None},
        {"src/wgsextract_cli/core/gamma.py": "VALUE = 4"},
        {"reference/input.csv": None},
        {"tests/test_alpha.py": None},
        {"native/README.txt": "more notes"},
        {"native/README.txt": None},
        {"gui/assets/logo.txt": None},
        {"unknown/config": "x"},
    ]
    before = fingerprint("job", repository=checkout(tmp_path / "before", FILES))
    for index, edit in enumerate(edits):
        root = checkout(checkout(tmp_path / str(index), FILES), edit)
        after = fingerprint("job", repository=root)
        selection = select_tests(list(edit), repository=root)
        selected = set(after.hashes) if selection == "all" else set(selection)
        assert not reusable(before, after) & selected, edit


def test_915_adding_a_test_file_keeps_unrelated_passes(tmp_path: Path) -> None:
    before = fingerprint("job", repository=checkout(tmp_path / "before", FILES))
    root = checkout(tmp_path / "after", FILES | {"tests/test_new.py": "import json"})
    after = fingerprint("job", repository=root)
    assert reusable(before, after) == set(before.hashes)


def test_915_passed_hashes_skip_pytest_and_are_listed_in_the_summary(
    tmp_path: Path,
) -> None:
    test = "tests/test_table_writer.py"
    job = "os=test tier=core"
    manifest = tmp_path / "main" / "passed.json"
    current = fingerprint(job)
    passed = Manifest()
    passed.record({current.hashes[test]}, current.names, NOW)
    passed.save(manifest)
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_run_tests.py"),
            "--selection",
            json.dumps([test]),
            "--cache-job",
            job,
            "--cache-read",
            f"main={manifest}",
            "--summary",
            str(summary),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "already passed" in result.stdout
    assert f"| `{test}` | main |" in summary.read_text(encoding="utf-8")


def test_915_missing_corrupt_or_stale_cache_runs_every_selected_test(
    tmp_path: Path,
) -> None:
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    stale = tmp_path / "stale.json"
    old = Manifest()
    old.record({"0" * 32}, frozenset(), NOW)
    old.save(stale)
    sources = {"missing": tmp_path / "missing.json", "corrupt": corrupt, "stale": stale}
    selected = ["tests/test_table_writer.py"]
    assert apply_cache(selected, "job", sources, None)[0] == selected
    # Without a usable cache an 'all' selection stays the unfiltered suite.
    assert apply_cache(None, "job", sources, None)[0] is None


def test_915_only_new_passing_hashes_mark_the_cache_for_saving(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "pr" / "passed.json"
    marker = tmp_path / "pr.updated"
    current = Fingerprint({"tests/test_a.py": "a" * 32}, frozenset({"pixi.lock"}))
    record_passes(manifest, ["tests/test_a.py"], current)
    assert marker.exists()
    marker.unlink()
    record_passes(manifest, ["tests/test_a.py"], current)
    assert not marker.exists()
    assert Manifest.load(manifest).passed_with("a" * 32, current.names)


def test_915_old_passes_expire_from_the_manifest() -> None:
    manifest = Manifest()
    manifest.record({"old"}, frozenset(), datetime(2026, 10, 1, tzinfo=timezone.utc))
    manifest.record(
        {"recent"}, frozenset(), datetime(2026, 11, 10, tzinfo=timezone.utc)
    )
    manifest.record({"new"}, frozenset(), NOW)
    assert set(manifest.passed) == {"recent", "new"}


REPO = "WGSExtract/wgsextract-cli"
SHA = "c" * 40
QUEUE_TESTS = ["tests/test_table_writer.py", "tests/test_microarray_vendor_formats.py"]


def run_suite(tmp_path: Path, *args: str) -> dict[str, Any]:
    """Run ci_run_tests with a passing stand-in pytest; return its scope report."""
    binary = tmp_path / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    pixi = binary / "pixi"
    pixi.write_text("#!/bin/sh\nexit 0\n")
    pixi.chmod(0o755)
    scope = tmp_path / "scope.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_run_tests.py"),
            "--cache-job",
            "os=test tier=all",
            "--scope",
            str(scope),
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=os.environ | {"PATH": f"{binary}{os.pathsep}{os.environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    report: dict[str, Any] = json.loads(scope.read_text(encoding="utf-8"))
    return report


@pytest.mark.skipif(os.name == "nt", reason="POSIX stand-in for pixi")
def test_1373_queue_and_main_together_run_every_test_file(tmp_path: Path) -> None:
    """Main reruns exactly what the queue skipped as cached, and nothing else."""
    cached, executed = QUEUE_TESTS
    current = fingerprint("os=test tier=all")
    main_manifest = Manifest()
    main_manifest.record({current.hashes[cached]}, current.names, NOW)
    main_manifest.save(tmp_path / "main" / "passed.json")
    queue = tmp_path / "queue" / "passed.json"

    queue_scope = run_suite(
        tmp_path / "group",
        *("--selection", json.dumps(QUEUE_TESTS)),
        *("--cache-read", f"main={tmp_path / 'main' / 'passed.json'}"),
        *("--record-executed", str(queue)),
    )
    assert queue_scope == {"ran": [executed], "skipped": {cached: "main"}}

    recorded = tmp_path / "main-after" / "passed.json"
    main_scope = run_suite(
        tmp_path / "push",
        *("--cache-read", f"merge-group-7={queue}"),
        *("--cache-record", str(recorded)),
    )
    assert main_scope["skipped"] == {executed: "merge-group-7"}
    assert cached in main_scope["ran"]
    every_test = set(main_scope["ran"]) | set(main_scope["skipped"])
    assert set(queue_scope["ran"]) | set(main_scope["ran"]) == every_test
    # Main's manifest still learns the queue's passes for later groups and PRs.
    assert Manifest.load(recorded).passed_with(current.hashes[executed], current.names)


@pytest.mark.skipif(os.name == "nt", reason="POSIX stand-in for pixi")
def test_1373_main_without_a_queue_manifest_runs_the_full_suite(
    tmp_path: Path,
) -> None:
    scope = run_suite(
        tmp_path,
        *("--cache-read", f"merge-group-7={tmp_path / 'missing.json'}"),
        *("--cache-record", str(tmp_path / "main" / "passed.json")),
    )
    assert scope["skipped"] == {}
    assert set(QUEUE_TESTS) <= set(scope["ran"])


def queue_api(**changes: Any) -> dict[str, Any]:
    """Fake GitHub responses for one passing merge group at SHA."""
    current = Fingerprint({"tests/test_a.py": "a" * 32}, frozenset({"pixi.lock"}))
    manifest = Manifest()
    manifest.record(set(current.hashes.values()), current.names, NOW)
    data = {
        "schema": 2,
        "names": manifest.names,
        "passed": {key: list(value) for key, value in manifest.passed.items()},
    }
    responses: dict[str, Any] = {
        "run": {
            "id": 7,
            "head_sha": SHA,
            "event": "merge_group",
            "head_branch": "gh-readonly-queue/main/pr-1-abc",
            "head_repository": {"full_name": REPO},
            "html_url": "https://github.com/x/runs/7",
        },
        "jobs": [{"name": "required", "conclusion": "success"}],
        "artifacts": [
            {
                "id": 9,
                "name": "test-pass-queue-ubuntu-all",
                "expired": False,
                "workflow_run": {"head_sha": SHA},
            }
        ],
        "manifest": json.dumps(data),
    }
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(responses[key], dict):
            responses[key] = responses[key] | value
        else:
            responses[key] = value
    if "zip" not in responses:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as files:
            files.writestr("passed.json", responses["manifest"])
        responses["zip"] = archive.getvalue()
    return responses


def fake_github(responses: dict[str, Any]) -> Any:
    def api(path: str) -> bytes:
        if "error" in responses:
            raise subprocess.CalledProcessError(1, ["gh", "api", path])
        if "/workflows/pr_ci.yml/runs?" in path:
            return json.dumps({"workflow_runs": [responses["run"]]}).encode()
        if path.endswith("/zip"):
            return bytes(responses["zip"])
        if "/jobs?" in path:
            jobs = responses["jobs"]
            return json.dumps({"total_count": len(jobs), "jobs": jobs}).encode()
        if "/artifacts?" in path:
            return json.dumps({"artifacts": responses["artifacts"]}).encode()
        raise AssertionError(path)

    return api


def test_1373_passing_merge_group_at_the_exact_sha_shares_its_manifest(
    tmp_path: Path,
) -> None:
    output = tmp_path / "merge-group" / "passed.json"
    run = fetch(
        REPO,
        SHA,
        "test-pass-queue-ubuntu-all",
        output,
        api=fake_github(queue_api()),
    )
    assert run == "https://github.com/x/runs/7"
    assert Manifest.load(output).passed_with("a" * 32, frozenset({"pixi.lock"}))


def test_1373_older_passing_group_at_the_sha_still_shares_its_manifest(
    tmp_path: Path,
) -> None:
    responses = queue_api()
    newer = responses["run"] | {"id": 8, "html_url": "https://github.com/x/runs/8"}
    api = fake_github(responses)

    def two_runs(path: str) -> bytes:
        if "/workflows/pr_ci.yml/runs?" in path:
            return json.dumps({"workflow_runs": [responses["run"], newer]}).encode()
        if "/runs/8/artifacts?" in path:
            return json.dumps({"artifacts": []}).encode()
        return bytes(api(path))

    output = tmp_path / "passed.json"
    run = fetch(REPO, SHA, "test-pass-queue-ubuntu-all", output, api=two_runs)
    assert run == "https://github.com/x/runs/7"
    assert output.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"error": True},
        {"run": {"head_sha": "d" * 40}},
        {"run": {"event": "push"}},
        {"run": {"head_branch": "main"}},
        {"run": {"head_repository": {"full_name": "someone/fork"}}},
        {"jobs": [{"name": "required", "conclusion": "failure"}]},
        {"jobs": [{"name": "required (draft)", "conclusion": "success"}]},
        {"artifacts": []},
        {
            "artifacts": [
                {"id": 9, "name": "test-pass-queue-ubuntu-all", "expired": True}
            ]
        },
        {"zip": b"not a zip"},
        {"manifest": "{not json"},
        {"zip": b"PK\x05\x06" + b"\x00" * 18},
    ],
    ids=lambda change: next(iter(change)),
)
def test_1373_missing_or_failed_merge_group_match_runs_the_full_suite(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    output = tmp_path / "merge-group" / "passed.json"
    run = fetch(
        REPO,
        SHA,
        "test-pass-queue-ubuntu-all",
        output,
        api=fake_github(queue_api(**changes)),
    )
    assert run is None
    assert not output.exists()
