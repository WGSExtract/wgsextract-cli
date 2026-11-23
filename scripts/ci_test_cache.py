"""Per-test-file result cache keyed on dependency hashes (#915, test result cache).

A test file's hash covers exactly the tracked files whose change would make
``select_tests`` pick it, plus every file whose change makes the selector fall
back to the full suite, plus the CI execution context. So two checkouts with
the same hash differ only in files the selector would not run that test for;
skipping on an exact hash match is never less safe than #914's selection.
Manifests only list hashes that passed. A lookup is exact set membership, so
restoring an older or unrelated manifest can cause a re-run, never a false skip.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_select_tests import ci_path, select_tests, selection_context  # noqa: E402
from scripts.ci_test_dependencies import ROOT  # noqa: E402

SCHEMA = 2
# How tests are run, not what they import: a change re-runs every test.
EXECUTION_INPUTS = (
    ".github/workflows/os_tests.yml",
    ".github/workflows/ubuntu_ci.yml",
    ".github/workflows/macos_ci.yml",
    ".github/workflows/windows_ci.yml",
    ".github/workflows/linux_arm64.yml",
    ".github/actions/setup-pixi/",
    "scripts/ci_impact_results.py",
    "scripts/ci_run_tests.py",
    "scripts/ci_test_tiers.py",
    "scripts/ci_test_cache.py",
    "scripts/ci_select_tests.py",
    "scripts/ci_test_dependencies.py",
)
# Runner identity from the GitHub-hosted image, when present.
RUNNER_VARIABLES = ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH")
MAX_ENTRIES = 4000
MAX_TREES = 8
MAX_AGE = timedelta(days=14)


def tracked_files(root: Path) -> list[str]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    return sorted(path for path in output.decode().split("\0") if path)


def deletion_selects_all(path: str) -> bool:
    """Whether the selector falls back to the full suite when ``path`` is gone.

    A hash sees only files that exist, so the names of these files are hashed
    for every test: deleting one must change every hash, as it selects all.
    """
    if path.endswith(".md"):
        return False
    if ci_path(path) and not path.startswith("tests/"):
        return False
    return path.endswith(".py") or not path.startswith(
        ("gui/", "native/", "site/", "site_src/")
    )


def file_digest(path: Path) -> str:
    if path.is_symlink():
        return "link:" + os.readlink(path)
    if not path.is_file():
        return "missing" if not path.exists() else "directory"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_context(job: str) -> str:
    """The job's partition plus the interpreter and runner that execute it."""
    parts = [
        f"schema={SCHEMA}",
        f"job={job}",
        f"python={sys.implementation.name}-{platform.python_version()}",
        f"platform={sys.platform}-{platform.machine()}",
        *(f"{name}={os.environ.get(name, '')}" for name in RUNNER_VARIABLES),
    ]
    return "\n".join(parts)


@dataclass(frozen=True)
class Fingerprint:
    """Per-test dependency hashes, and the files whose deletion selects all."""

    hashes: dict[str, str]
    names: frozenset[str]


def fingerprint(job: str, *, repository: Path = ROOT) -> Fingerprint:
    """Hash everything each collected test file depends on."""
    root = repository.resolve()
    files = tracked_files(root)
    shared: set[str] = {
        path
        for path in files
        if any(
            path == prefix or prefix.endswith("/") and path.startswith(prefix)
            for prefix in EXECUTION_INPUTS
        )
    }
    dependencies: defaultdict[str, set[str]] = defaultdict(set)
    for path in files:
        selection = select_tests([path], repository=root)
        if selection == "all":
            shared.add(path)
        else:
            for test in selection:
                dependencies[test].add(path)
    tests = selection_context(root).tests
    for test in tests:
        dependencies[test].add(test)
    digests = {
        path: file_digest(root / path) for path in shared.union(*dependencies.values())
    }

    base = hashlib.sha256(runtime_context(job).encode())
    for path in sorted(shared):
        base.update(f"shared\0{path}\0{digests[path]}\0".encode())
    hashes = {}
    for test in tests:
        digest = base.copy()
        # Two tests with identical dependency sets are still distinct results.
        digest.update(f"test\0{test}\0".encode())
        for path in sorted(dependencies[test] - shared):
            digest.update(f"input\0{path}\0{digests[path]}\0".encode())
        hashes[test] = digest.hexdigest()[:32]
    return Fingerprint(hashes, frozenset(filter(deletion_selects_all, files)))


def names_id(names: frozenset[str]) -> str:
    return hashlib.sha256("\0".join(sorted(names)).encode()).hexdigest()[:16]


@dataclass
class Manifest:
    """Passed hashes, each with the deletion-sensitive file names of its tree.

    Hashes cover only files that exist. A pass is reusable only if none of
    its tree's deletion-sensitive files has since been deleted; additions
    need no such check, as they enter the hashes of the tests they affect.
    """

    passed: dict[str, tuple[str, str]] = field(default_factory=dict)
    names: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Manifest:
        """Read a restored manifest; anything unreadable is an empty cache."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["schema"] != SCHEMA:
                return cls()
            names = {
                key: [str(name) for name in value]
                for key, value in data["names"].items()
            }
            passed = {
                str(key): (str(day), str(tree))
                for key, (day, tree) in data["passed"].items()
                if tree in names
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return cls()
        return cls(passed, names)

    def passed_with(self, digest: str, names: frozenset[str]) -> bool:
        entry = self.passed.get(digest)
        return entry is not None and names.issuperset(self.names[entry[1]])

    def record(self, hashes: set[str], names: frozenset[str], now: datetime) -> bool:
        """Add passing hashes; return whether anything new was learned."""
        tree = names_id(names)
        new = {key for key in hashes if self.passed.get(key, ("", ""))[1] != tree}
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.names[tree] = sorted(names)
        self.passed.update(dict.fromkeys(hashes, (stamp, tree)))
        cutoff = (now - MAX_AGE).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent = sorted(
            ((entry, key) for key, entry in self.passed.items() if entry[0] >= cutoff),
            reverse=True,
        )[:MAX_ENTRIES]
        # Newest trees first; each keeps one list of names, so bound them too.
        trees = list(dict.fromkeys(entry[1] for entry, _ in recent))[:MAX_TREES]
        self.passed = {key: entry for entry, key in recent if entry[1] in trees}
        self.names = {key: value for key, value in self.names.items() if key in trees}
        return bool(new)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema": SCHEMA,
            "names": self.names,
            "passed": {key: list(entry) for key, entry in sorted(self.passed.items())},
        }
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def skip_summary(
    candidates: list[str], skipped: dict[str, str], hashes: dict[str, str]
) -> str:
    lines = [
        "### Test result cache (#915)",
        "",
        f"{len(candidates)} selected test files; {len(skipped)} skipped because "
        f"they already passed with identical inputs; "
        f"{len(candidates) - len(skipped)} run.",
    ]
    if skipped:
        lines += [
            "",
            "<details><summary>Cache-skipped test files</summary>",
            "",
            "| Test file | Passed on | Dependency hash |",
            "|---|---|---|",
            *(
                f"| `{test}` | {source} | `{hashes[test]}` |"
                for test, source in sorted(skipped.items())
            ),
            "",
            "</details>",
        ]
    return "\n".join(lines) + "\n"
