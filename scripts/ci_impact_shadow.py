"""Publish advisory Linux PR test selections against the actual tested tree."""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import html
import json
import platform
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.ci_impact_diff import ImpactDiff, git_blob, impact_diff
from scripts.ci_impact_observations import SCHEMA
from scripts.ci_impact_results import file_results
from scripts.ci_impact_scopes import (
    ChangedScopes,
    changed_scopes,
    decisive_module_scope,
    module_contract_tests,
    module_scopes,
    shadow_candidates,
)
from scripts.ci_impact_static import StaticUsers, tracked_python
from scripts.ci_impact_symbols import SYMBOL_CONTRACTS, symbol_delta
from scripts.ci_impact_wide import command_groups, sample_callers
from scripts.ci_run_tests import test_targets
from scripts.ci_select_tests import CI_TESTS, ci_path
from scripts.ci_test_dependencies import docs_test_targets, test_inputs
from scripts.ci_test_tiers import SINGLE_OS_MODULES

SOURCE = "src/wgsextract_cli/"
VARIANTS = ("decisive", "tight", "symbol", "widely_called")
LOCKS = {"pixi.toml", "pixi.lock", "pyproject.toml"}
IMPORT_SMOKE = "tests/test_ci_package_import_smoke.py"


def observation_map(
    root: Path, file: Path, base: str
) -> tuple[dict[str, Any], str | None]:
    if not file.is_file():
        return {}, "No trusted scheduled Linux observation map available."
    try:
        with gzip.open(file, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        meta = data["manifest"]
        sha = meta["source_sha"]
        if (
            meta["schema"] != SCHEMA
            or meta["platform"] != "Linux"
            or meta["machine"] != "x86_64"
            or meta["profile"] != "ubuntu-x64/all/skip-independent=false"
            or meta["python"] != platform.python_version()
            or meta["run_id"] < 1
            or meta["attempt"] < 1
            or not data["observed_tests"]
            or len(sha) != 40
            or any(c not in "0123456789abcdef" for c in sha)
        ):
            raise ValueError
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, base],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if ancestry.returncode:
            return {}, "Observation SHA is outside the available tested-base history."
        lock = hashlib.sha256(git_blob(root, base, "pixi.lock")).hexdigest()
        if meta["lock_sha256"] != lock:
            return {}, "Observation map and tested base use different Pixi locks."
        if not all(
            isinstance(data.get(key), dict)
            for key in ("modules", "functions", "symbol_reads", "seconds")
        ):
            raise ValueError
        return data, None
    except (
        EOFError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.CalledProcessError,
    ):
        return {}, "Invalid or incomplete Linux observation map."


def compute(
    root: Path, diff: ImpactDiff, tests: set[str], evidence: dict[str, Any]
) -> tuple[dict[str, list[str]], dict[str, set[str]], list[str]]:
    reasons: dict[str, set[str]] = defaultdict(set)
    paths = {path for row in diff.changes for path in (row.before, row.after) if path}
    resources = test_inputs(root)
    shared = {
        t.split("::", 1)[0] for t in docs_test_targets(sorted(paths), repository=root)
    }
    observed_calls: set[str] = set()
    wide_calls: set[str] = set()
    scopes = {}
    module_users = {}
    symbol_users = {}
    unmodeled = []
    for path in sorted(paths):
        if path in LOCKS or path in {"conftest.py", "tests/conftest.py"}:
            reasons[path].add("universal-interpreter-or-root-pytest")
        if path.endswith(".py") and path.startswith(SOURCE):
            shared.add("tests/test_text_newlines.py")
            reasons[path].add("source-tree-newlines")
            if path.startswith((SOURCE + "commands/", SOURCE + "cli/")):
                shared.add("tests/test_command_needs.py")
                reasons[path].add("registered-command-tool-contract")
        if path in tests:
            shared.add(path)
            reasons[path].add("changed-test")
        if ci_path(path):
            shared.update(CI_TESTS)
            reasons[path].add("ci-policy")
        readers = {
            test
            for pattern, owners in resources.items()
            if fnmatch.fnmatchcase(path, pattern)
            for test in owners
        }
        if readers:
            shared.update(readers)
            reasons[path].add("declared-or-static-input-readers")
    if any("universal-interpreter-or-root-pytest" in r for r in reasons.values()):
        return {name: sorted(tests) for name in VARIANTS}, reasons, []
    old_users = new_users = None
    modules: dict[str, list[str]] = evidence.get("modules", {})
    functions: dict[str, dict[str, list[str]]] = evidence.get("functions", {})
    readers_by_symbol: dict[str, dict[str, list[str]]] = evidence.get(
        "symbol_reads", {}
    )
    observed = set(evidence.get("observed_tests", ()))
    groups = command_groups(modules)
    for change in diff.changes:
        source = change.after or change.before
        assert source is not None
        if not source.endswith(".py") or not source.startswith((SOURCE, "tests/")):
            if not reasons[source] and not source.endswith(".md"):
                unmodeled.append(source)
            continue
        if source in tests:
            continue
        if old_users is None:
            old_users = StaticUsers(root, diff.base)
            new_users = StaticUsers(root, diff.head)
        assert new_users is not None
        trees = [
            (p, sha, index)
            for p, sha, index in (
                (change.before, diff.base, old_users),
                (change.after, diff.head, new_users),
            )
            if p
        ]
        importers = set().union(
            *(index.historical_importers(p, tests) for p, _, index in trees)
        )
        seen_in_module = set().union(*(set(modules.get(p, ())) for p, _, _ in trees))
        if change.status != "M" or not source.startswith(SOURCE):
            if any(
                p.startswith(SOURCE) and p.endswith("/__init__.py") for p, _, _ in trees
            ):
                reasons[source].add(
                    "universal-package-initializer-added-removed-or-moved"
                )
                return {name: sorted(tests) for name in VARIANTS}, reasons, []
            shared.update(importers | seen_in_module)
            fixture = source.startswith("tests/") and source.endswith("/conftest.py")
            if fixture:
                shared.update(
                    t for t in tests if t.startswith(source.rpartition("/")[0] + "/")
                )
            reasons[source].add("historical-importers-or-fixture-scope")
            if not (
                importers
                or seen_in_module
                or fixture
                or "declared-or-static-input-readers" in reasons[source]
            ):
                unmodeled.append(source)
            continue
        try:
            before, after = (git_blob(root, sha, p) for p, sha, _ in trees)
            before_scopes, after_scopes = module_scopes(before), module_scopes(after)
            change_scope = changed_scopes(before_scopes, after_scopes)
            delta = symbol_delta(
                before, after, package_init=source.endswith("/__init__.py")
            )
        except (SyntaxError, ValueError):
            unmodeled.append(source)
            continue
        if source.endswith("/__init__.py") and (
            change_scope.initialization or delta.broad
        ):
            reasons[source].add("universal-package-initialization")
            return (
                {name: sorted(tests) for name in VARIANTS},
                reasons,
                sorted(set(unmodeled)),
            )
        if delta.broad and not change_scope.initialization:
            change_scope = ChangedScopes(True, change_scope.functions)
        scopes[source] = change_scope
        contracts = module_contract_tests(source, tests, SYMBOL_CONTRACTS)
        if (change_scope.initialization or change_scope.functions) and not (
            importers or seen_in_module or contracts
        ):
            unmodeled.append(source)
        for fn in sorted(change_scope.functions):
            callers = set(functions.get(source, {}).get(fn, ()))
            name = fn.rsplit(".", 1)[-1]
            if not callers or fn not in before_scopes.functions:
                shared.update(importers | contracts)
                for p, _, index in trees:
                    shared.update(index.symbol_users(p, {name}, tests, functions))
                reasons[source].add("unobserved-function-static-callers")
            else:
                reasons[source].add("observed-function")
            observed_calls.update(callers)
            sampled = sample_callers(
                source,
                fn,
                callers,
                observed,
                groups,
                contracts,
                change=change_scope,
                existed=fn in before_scopes.functions,
            )
            wide_calls.update(sampled.selected)
        legacy_module = decisive_module_scope(
            before, after, before_scopes, after_scopes
        )
        if change_scope.initialization or legacy_module:
            module_users[source] = importers | seen_in_module | contracts
            reasons[source].add("decisive-module-users")
        if change_scope.initialization:
            reasons[source].add("module-initialization-users")
            if not delta.broad and (delta.imports or delta.symbols):
                scoped = set(contracts)
                if delta.imports:
                    scoped.add(IMPORT_SMOKE)
                    reasons[source].add("eager-import-smoke")
                if delta.symbols:
                    reasons[source].add("structural-symbol-users")
                    for fn, users in functions.get(source, {}).items():
                        if fn.split(".", 1)[0] in delta.symbols:
                            scoped.update(users)
                    for name in delta.symbols:
                        scoped.update(readers_by_symbol.get(source, {}).get(name, ()))
                    for p, _, index in trees:
                        scoped.update(
                            index.symbol_users(p, delta.symbols, tests, functions)
                        )
                symbol_users[source] = scoped
        elif legacy_module:
            symbol_users[source] = contracts
        if contracts:
            shared.update(contracts)
    # Overlapping non-sampled reasons must never be subtracted from wide.
    return (
        shadow_candidates(
            shared | observed_calls,
            module_users,
            scopes,
            tests,
            SYMBOL_CONTRACTS,
            symbol_module_users=symbol_users,
            widely_called_shared=shared | wide_calls,
        ),
        reasons,
        sorted(set(unmodeled)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    diff = impact_diff(root, "HEAD^1", "HEAD")
    source_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD^2"], text=True
    ).strip()
    tests = set(
        test_targets(
            json.dumps(
                sorted(
                    path
                    for path in tracked_python(root, diff.head)
                    if path.startswith("tests/")
                    and Path(path).name.startswith("test_")
                    and Path(path).name not in SINGLE_OS_MODULES
                )
            )
        )
        or ()
    )
    evidence, gap = observation_map(root, args.observations, diff.base)
    candidates, reasons, unmatched = compute(root, diff, tests, evidence)
    actual_selection = test_targets(args.selection)
    requested = tests if actual_selection is None else tests & set(actual_selection)
    actual: dict[str, Any] = {}
    unmapped = 0
    receipt = "No regular pytest receipt (not run, cache-only, or interrupted)."
    if args.junit.is_file():
        try:
            actual, unmapped = file_results(args.junit, tests)
            receipt = "Local pytest JUnit receipt."
        except (ET.ParseError, OSError, ValueError):
            receipt = "Pytest JUnit receipt is incomplete or invalid."
    failures = {t for t, r in actual.items() if r["failed"]}
    passed = {t for t, r in actual.items() if r["passed"] and not r["failed"]}
    completed = failures | passed
    seconds = evidence.get("seconds", {})
    baseline_timed = requested & seconds.keys()
    baseline_minutes = (
        round(sum(seconds[t] for t in baseline_timed) / 60, 3)
        if baseline_timed
        else None
    )
    report: dict[str, Any] = {
        "schema": 1,
        "run_id": args.run_id,
        "attempt": args.attempt,
        "tested_tree": diff.head,
        "tested_base": diff.base,
        "pr_head": source_head,
        "observations": evidence.get("manifest"),
        "observation_gap": gap,
        "unmatched_inputs": unmatched,
        "actual_selected": sorted(requested),
        "actual_estimated_test_minutes": baseline_minutes,
        "actual_timed_files": len(baseline_timed),
        "actual_results": actual,
        "unmapped_cases": unmapped,
        "run_outcome": args.outcome,
        "receipt": receipt,
        "rule_reasons": {p: sorted(r) for p, r in reasons.items()},
        "variants": {},
    }
    summary = [
        "### Linux x64 test selection shadow (advisory)",
        f"Actual selector: {len(requested)} files before cache; {len(completed)} files with pass/fail observations.",
        receipt,
        gap or f"Observation map: `{evidence['manifest']['source_sha']}`.",
        "",
        "| Candidate | Files | Est. test-min | Timing files | Observed omitted failures | Unobserved selected |",
        "|---|---:|---:|---:|---:|---:|",
        f"| current | {len(requested)} | {baseline_minutes if baseline_minutes is not None else 'n/a'} | "
        f"{len(baseline_timed)} | {len(failures - requested)} | {len(requested - completed)} |",
    ]
    for name, targets in candidates.items():
        selected = set(targets)
        timed = selected & seconds.keys()
        minutes = round(sum(seconds[t] for t in timed) / 60, 3) if timed else None
        omitted_failures = sorted(failures - selected)
        report["variants"][name] = {
            "selected": targets,
            "omitted": sorted(tests - selected),
            "estimated_test_minutes": minutes,
            "timed_files": len(timed),
            "observed_omitted_failures": omitted_failures,
            "observed_omitted_passes": sorted(passed - selected),
            "selected_without_pass_or_fail": sorted(selected - completed),
        }
        summary.append(
            f"| {name} | {len(selected)} | {minutes if minutes is not None else 'n/a'} | "
            f"{len(timed)} | {len(omitted_failures)} | {len(selected - completed)} |"
        )
    for name, targets in candidates.items():
        summary.extend(
            (
                "",
                f"<details><summary>{name}: selected files</summary>",
                "",
                *(f"<code>{html.escape(test)}</code><br>" for test in targets),
                "",
                "</details>",
            )
        )
    report["comparable"] = bool(
        gap is None
        and not unmatched
        and completed
        and args.outcome in {"success", "failure"}
        and unmapped == 0
    )
    summary.extend(
        (
            "",
            f"Unmatched inputs: {len(unmatched)}. Unmapped JUnit cases: {unmapped}.",
            "An omitted failure is a triage lead, not proof of PR origin. "
            "Untested, skipped, cached and certified work is not a pass observation.",
            "Times are summed pytest case durations, not runner wall time; unknown timings are excluded.",
            "See the `test-selection-shadow` artifact for selected files and details.",
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with args.summary.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(summary) + "\n")


if __name__ == "__main__":
    main()
