"""Plan only the authoritative regular Linux pytest selection."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.ci_impact_diff import ChangedPath, ImpactDiff, impact_diff
from scripts.ci_impact_shadow import (
    LOCKS,
    SOURCE,
    GateDecision,
    compute,
    lane_tests,
    observation_map,
    parse_selection,
)
from scripts.ci_impact_static import module_name, parse_python, tracked_python
from scripts.ci_select_tests import ImportGraph


def _import_graph_at(root: Path, rev: str) -> ImportGraph:
    blobs = tracked_python(root, rev)

    def parse(file: Path) -> ast.Module:
        return parse_python(root, blobs[file.relative_to(root).as_posix()])

    return ImportGraph(
        root,
        files={module_name(path): root / path for path in blobs},
        parse=parse,
    )


def _static_drift_importers(
    root: Path, drift: ImpactDiff, tests: set[str]
) -> set[str] | None:
    """Prove a nonempty module-wide import closure for each unobserved change."""
    proved: set[int] = set()
    selected: set[str] = set()
    for rev, before in ((drift.base, True), (drift.head, False)):
        paths = [
            (index, module_name(path))
            for index, change in enumerate(drift.changes)
            if (path := change.before if before else change.after)
        ]
        if not paths:
            continue
        graph = _import_graph_at(root, rev)
        if any(not name.startswith("scripts.") for name in graph.unsafe):
            return None
        for test in tests:
            name = module_name(test)
            if name not in graph.files:
                continue
            directory = (root / test).parent
            closure = graph.reaches(name) | graph.fixture_dependencies(directory)
            if closure & graph.unsafe:
                return None
            relevant = [(index, module) for index, module in paths if module in closure]
            if relevant:
                selected.add(test)
                actual = graph.reaches(
                    name, imports_only=True
                ) | graph.fixture_dependencies(directory, imports_only=True)
                proved.update(index for index, module in relevant if module in actual)
    return selected if len(proved) == len(drift.changes) else None


def plan(
    root: Path,
    *,
    event: str,
    base: str,
    observations: Path,
    legacy: str,
    disabled: bool,
) -> GateDecision:
    """The kill switch and evidence gaps use the untouched existing selection."""
    try:
        selection = parse_selection(json.loads(legacy) if legacy != "all" else legacy)
    except ValueError as error:
        raise ValueError(
            "--legacy must be 'all' or a JSON array of test paths"
        ) from error

    def fallback(mode: str) -> GateDecision:
        return {"mode": mode, "selection": selection}

    if disabled or event not in {"pull_request", "push"}:
        return fallback("legacy-disabled-or-outside-lane")
    diff = impact_diff(root, base, "HEAD")
    if event == "push" and base != diff.base:
        return fallback("legacy-non-linear-push")
    evidence, gap = observation_map(root, observations, diff.base)
    if gap:
        return fallback("legacy-observation-gap")
    manifest = evidence["manifest"]
    recorded = manifest.get("recorded_at")
    if recorded is None:
        recorded = subprocess.check_output(
            ["git", "show", "-s", "--format=%cI", manifest["source_sha"]],
            cwd=root,
            text=True,
        ).strip()
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(recorded)
    except (ValueError, TypeError):
        return fallback("legacy-stale-map")
    if not timedelta(minutes=-5) <= age <= timedelta(hours=48):
        return fallback("legacy-stale-map")
    tests = lane_tests(root, diff.head, skip_independent=event == "pull_request")
    candidates, reasons, unmatched = compute(root, diff, tests, evidence)
    if any(
        reason.startswith("universal-")
        for rules in reasons.values()
        for reason in rules
    ):
        return {"mode": "universal-full", "selection": "all"}
    if unmatched or not candidates["symbol"]:
        return fallback("legacy-unmatched-or-empty")
    drift = impact_diff(root, manifest["source_sha"], diff.base)
    extra: set[str] = set()
    unobserved: list[ChangedPath] = []
    for change in drift.changes:
        needs_static = False
        for path in (change.before, change.after):
            if path is None:
                continue
            if path in tests:
                extra.add(path)
            elif (
                path in LOCKS
                or path == "conftest.py"
                or (
                    path.startswith("tests/")
                    and not path.rsplit("/", 1)[-1].startswith("test_")
                )
            ):
                return fallback("legacy-test-harness-changed-since-map")
            elif path.startswith(SOURCE):
                users = evidence["modules"].get(path, ())
                extra.update(users)
                needs_static |= not users
        if needs_static:
            unobserved.append(change)
    if unobserved:
        if any(
            not path.startswith(SOURCE)
            or not path.endswith(".py")
            or path.endswith(("/__init__.py", "/__main__.py"))
            for change in unobserved
            for path in (change.before, change.after)
            if path
        ):
            return fallback("legacy-unobserved-source-drift")
        unknown = ImpactDiff(drift.base, drift.head, tuple(unobserved))
        importers = _static_drift_importers(root, unknown, tests)
        if importers is None:
            return fallback("legacy-unobserved-source-drift")
        static, _, _ = compute(root, unknown, tests, evidence)
        extra.update(importers | set(static["symbol"]))
    return {
        "mode": "symbol",
        "selection": sorted(set(candidates["symbol"]) | (extra & tests)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--legacy", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disabled", action="store_true")
    parser.add_argument("--verify-wiring", action="store_true")
    args = parser.parse_args()
    try:
        decision = plan(
            Path.cwd().resolve(),
            event=args.event,
            base=args.base,
            observations=args.observations,
            legacy=args.legacy,
            disabled=args.disabled,
        )
    except ValueError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(decision) + "\n", encoding="utf-8")
    if args.verify_wiring:
        mode = decision["mode"]
        if mode in {"symbol", "universal-full"}:
            print(f"Verified fresh Linux selector map: {mode}.")
        else:
            print(
                f"::warning::Linux symbol gate did not activate: {mode}; using current selection."
            )
            raise SystemExit(3)


if __name__ == "__main__":
    main()
