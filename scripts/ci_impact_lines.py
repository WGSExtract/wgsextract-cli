"""Export and read data-only line coverage for the advisory Linux selector."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import zlib
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from scripts.ci_impact_diff import ImpactDiff, git_blob
from scripts.ci_impact_observations import SOURCE, observed_files
from scripts.ci_impact_scopes import changed_scopes, module_scopes
from scripts.ci_impact_symbols import symbol_delta

SCHEMA = 1
MAX_COMPRESSED = 8 * 1024**2
MAX_UNCOMPRESSED = 64 * 1024**2


def fingerprints(root: Path, sha: str) -> dict[str, str]:
    # Include fixtures outside tests/, runner config and all other tracked inputs.
    groups = {name: hashlib.sha256() for name in ("source", "tests", "other_inputs")}
    listing = subprocess.check_output(["git", "ls-tree", "-r", "-z", sha], cwd=root)
    for row in listing.split(b"\0")[:-1]:
        path = row.split(b"\t", 1)[1]
        group = (
            "source"
            if path.startswith(SOURCE.encode())
            else "tests"
            if path.startswith(b"tests/")
            else "other_inputs"
        )
        groups[group].update(row + b"\0")
    return {group: value.hexdigest() for group, value in groups.items()}


def line_callers(
    root: Path,
    diff: ImpactDiff,
    line_map: Path,
    evidence: dict[str, Any],
    tests: set[str],
) -> tuple[tuple[str, str, set[str]] | None, str]:
    """Only substitute callers for a single existing body with a line preimage."""
    manifest = evidence.get("manifest")
    if not manifest or not line_map.is_file():
        return None, "no-paired-map"
    source_changes = [
        row
        for row in diff.changes
        if any(path and path.startswith(SOURCE) for path in (row.before, row.after))
    ]
    if len(source_changes) != 1:
        return None, "not-single-source"
    (change,) = source_changes
    path = change.before
    if change.status != "M" or not path or not path.endswith(".py"):
        return None, "not-modified-python"
    if any(
        row != change and any(p and p not in tests for p in (row.before, row.after))
        for row in diff.changes
    ):
        return None, "mixed-input-change"
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(
            manifest["recorded_at"]
        )
        if not timedelta(minutes=-5) <= age <= timedelta(hours=48):
            return None, "stale-map"
        before, after = (git_blob(root, ref, path) for ref in (diff.base, diff.head))
        old, new = module_scopes(before), module_scopes(after)
        scope = changed_scopes(old, new)
        delta = symbol_delta(before, after, package_init=path.endswith("/__init__.py"))
        if scope.initialization or len(scope.functions) != 1 or delta.broad:
            return None, "not-single-existing-body"
        (name,) = scope.functions
        old_bodies, new_bodies = (
            old.functions.get(name, ()),
            new.functions.get(name, ()),
        )
        if len(old_bodies) != 1 or len(new_bodies) != 1 or "<locals>" in name:
            return None, "not-single-existing-body"
        if any(
            fn.startswith(name + ".<locals>.") for fn in old.functions | new.functions
        ):
            return None, "nested-body"
        left, right = old_bodies[0], new_bodies[0]
        required: set[int] = set()
        for tag, a, b, c, d in SequenceMatcher(
            None, before.splitlines(), after.splitlines(), autojunk=False
        ).get_opcodes():
            if tag == "equal":
                continue
            if (
                a == b
                or not left.first_line <= a + 1 <= b <= left.last_line
                or (c != d and not right.first_line <= c + 1 <= d <= right.last_line)
            ):
                return None, "unanchored-edit"
            required.update(range(a + 1, b + 1))
        function_callers = set(evidence["functions"].get(path, {}).get(name, ()))
        if not required or not function_callers:
            return None, "missing-function-coverage"
        if line_map.stat().st_size > MAX_COMPRESSED:
            return None, "oversize-map"
        with gzip.open(line_map, "rb") as stream:
            payload = stream.read(MAX_UNCOMPRESSED + 1)
        if len(payload) > MAX_UNCOMPRESSED:
            return None, "oversize-map"
        data = json.loads(payload)
        meta = data["manifest"]
        if meta["schema"] != SCHEMA or any(
            meta[key] != manifest[key] for key in ("source_sha", "run_id", "attempt")
        ):
            return None, "mismatched-map"
        if meta["fingerprints"] != fingerprints(root, diff.base):
            return None, "input-drift"
        table = data["tests"]
        if (
            not isinstance(table, list)
            or not all(isinstance(test, str) for test in table)
            or len(set(table)) != len(table)
            or not set(table) <= set(evidence["observed_tests"])
        ):
            return None, "invalid-map"
        rows = data["lines"].get(path, ())
        line = 0
        callers: set[str] = set()
        for offset, *ids in rows:
            if type(offset) is not int or offset < 1:
                return None, "invalid-map"
            line += offset
            if line not in required:
                continue
            if not ids or any(
                type(i) is not int or not 0 <= i < len(table) for i in ids
            ):
                return None, "invalid-map"
            users = {table[i] for i in ids}
            if not users <= function_callers:
                return None, "inconsistent-callers"
            required.remove(line)
            callers.update(users)
        if required or not callers & tests:
            return None, "unobserved-lines"
        return (path, name, callers), "eligible"
    except (
        AttributeError,
        EOFError,
        KeyError,
        OSError,
        RecursionError,
        SyntaxError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
        zlib.error,
    ):
        return None, "invalid-or-unavailable-map"


def main() -> None:
    import coverage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if not args.coverage_file.is_file():
        parser.error("coverage database missing")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "diff", "--quiet", sha, "--"], check=True)
    tracked = set(
        subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0")
    )
    data = coverage.CoverageData(basename=str(args.coverage_file))
    data.read()
    test_ids: dict[str, int] = {}
    lines: dict[str, list[list[int]]] = {}
    for filename in sorted(data.measured_files()):
        file = (root / filename).resolve()
        if not file.is_relative_to(root):
            continue
        path = file.relative_to(root).as_posix()
        if path not in tracked or not path.startswith(SOURCE) or file.suffix != ".py":
            continue
        previous = 0
        rows = []
        for line, contexts in sorted(data.contexts_by_lineno(filename).items()):
            ids = [
                test_ids.setdefault(test, len(test_ids))
                for test in sorted(observed_files(contexts, tracked))
            ]
            if ids:
                rows.append([line - previous, *sorted(ids)])
                previous = line
        if rows:
            lines[path] = rows
    if not lines:
        parser.error("coverage database contains no attributed package lines")
    payload = json.dumps(
        {
            "manifest": {
                "schema": SCHEMA,
                "source_sha": sha,
                "run_id": args.run_id,
                "attempt": args.attempt,
                "fingerprints": fingerprints(root, sha),
            },
            "tests": list(test_ids),
            "lines": lines,
        },
        separators=(",", ":"),
    ).encode()
    compressed = gzip.compress(payload, mtime=0)
    if len(payload) > MAX_UNCOMPRESSED or len(compressed) > MAX_COMPRESSED:
        parser.error("line map exceeds the advisory size budget")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(compressed)
    print(
        f"Linux line map: {len(compressed):,} bytes; {len(lines):,} source files; "
        f"{len(test_ids):,} test files."
    )


if __name__ == "__main__":
    main()
