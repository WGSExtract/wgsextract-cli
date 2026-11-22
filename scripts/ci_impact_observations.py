"""Convert pytest-cov contexts into compact, data-only test-impact evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

import coverage

from scripts.ci_impact_scopes import module_scopes

SCHEMA = 1
PHASES = {"setup", "run", "teardown"}


class Observations(TypedDict):
    modules: dict[str, list[str]]
    functions: dict[str, dict[str, list[str]]]
    observed_tests: list[str]


def _context_file(context: str, tracked: set[str]) -> str | None:
    node, separator, phase = context.rpartition("|")
    if not separator or phase not in PHASES:
        return None
    test, separator, _ = node.partition("::")
    if separator and test.startswith("tests/") and test in tracked:
        return test
    return None


def read_observations(
    data: coverage.CoverageData, root: Path, tracked: set[str]
) -> Observations:
    modules: dict[str, list[str]] = {}
    functions: dict[str, dict[str, list[str]]] = {}
    observed_tests: set[str] = set()
    for filename in sorted(data.measured_files()):
        # Do not export checkout-external paths or the contents of tests' inputs.
        path = (root / filename).resolve()
        if not path.is_relative_to(root):
            continue
        name = path.relative_to(root).as_posix()
        if name not in tracked or path.suffix != ".py":
            continue
        hits = data.contexts_by_lineno(filename)
        scope = module_scopes(path.read_bytes())
        test_files: set[str] = set()
        by_function: dict[str, set[str]] = defaultdict(set)
        # Visit innermost scopes first. An enclosing body's span includes its
        # nested definitions even though their bodies execute separately.
        owners: dict[int, str] = {}
        spans = sorted(
            (
                (body.last_line - body.first_line, body.first_line, body.last_line, fn)
                for fn, bodies in scope.functions.items()
                for body in bodies
            )
        )
        for _, first, last, fn in spans:
            for line in range(first, last + 1):
                owners.setdefault(line, fn)
        for line, contexts in hits.items():
            files = _observed_files(contexts, tracked)
            test_files.update(files)
            if (
                files
                and (owner := owners.get(line))
                and len(scope.functions[owner]) == 1
            ):
                by_function[owner].update(files)
        if test_files:
            modules[name] = sorted(test_files)
            observed_tests.update(test_files)
        if by_function:
            functions[name] = {
                fn: sorted(files) for fn, files in sorted(by_function.items())
            }
    return {
        "modules": modules,
        "functions": functions,
        "observed_tests": sorted(observed_tests),
    }


def _observed_files(contexts: Iterable[str], tracked: set[str]) -> set[str]:
    return {test for context in contexts if (test := _context_file(context, tracked))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if not args.coverage_file.is_file():
        parser.error(f"coverage database not found: {args.coverage_file}")
    data = coverage.CoverageData(basename=str(args.coverage_file))
    data.read()
    tracked = set(
        subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0")
    )
    observations = read_observations(data, root, tracked)
    if not observations["observed_tests"]:
        parser.error("coverage database contains no attributed test contexts")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    lock_sha256 = hashlib.sha256((root / "pixi.lock").read_bytes()).hexdigest()
    manifest = {
        "schema": SCHEMA,
        "source_sha": sha,
        "run_id": args.run_id,
        "attempt": args.attempt,
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "lock_sha256": lock_sha256,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output, "wt", encoding="utf-8") as stream:
        json.dump({"manifest": manifest, **observations}, stream, separators=(",", ":"))


if __name__ == "__main__":
    main()
