"""#915 (test result cache): skip only test files whose exact inputs passed."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

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
