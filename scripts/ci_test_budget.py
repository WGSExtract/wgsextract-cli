"""Measure advisory test-suite growth from regular Ubuntu pytest JUnit."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

BASELINE = Path(__file__).with_name("ci_test_budget_baseline.json")
TOTAL = "Full suite"
EXCESS = 0.25
MAX_JUNIT_BYTES = 16 * 1024 * 1024
# Selected by the runner, but all their cases are excluded by `-m 'not remote'`.
REMOTE_ONLY = {
    "tests/test_remote_benchmark.py",
    "tests/test_remote_benchmark_chm13.py",
    "tests/test_remote_downloads.py",
    "tests/test_remote_resources.py",
}


@dataclass(frozen=True)
class Cost:
    seconds: float = 0
    cases: int = 0
    skip_signature: str = ""

    def __add__(self, other: Cost) -> Cost:
        return Cost(
            self.seconds + other.seconds,
            self.cases + other.cases,
        )


@dataclass(frozen=True)
class Baseline:
    run: int
    modules: dict[str, Cost]
    targets: dict[str, Cost]


def area(path: str) -> str:
    from scripts.ci_optional_tools import SUITES
    from scripts.ci_select_tests import CI_TESTS
    from scripts.ci_test_dependencies import DOCS_TEST_MODULES
    from scripts.ci_test_tiers import INTEGRATION_MODULES, fast_test

    if path in CI_TESTS:
        return "CI policy"
    if path in DOCS_TEST_MODULES:
        return "Docs & CLI reference"
    if path in SUITES:
        return "Optional tools"
    if not fast_test(path):
        return "CLI nightly fallback exclusions"
    if Path(path).name in INTEGRATION_MODULES:
        return "CLI integration"
    return "CLI regular"


def totals(modules: Mapping[str, Cost]) -> dict[str, Cost]:
    result = {TOTAL: Cost()}
    for path, cost in modules.items():
        for key in (area(path), TOTAL):
            result[key] = result.get(key, Cost()) + cost
    return result


def test_files(root: Path) -> set[str]:
    return {
        file.relative_to(root).as_posix()
        for pattern in ("test_*.py", "*_test.py")
        for file in (root / "tests").rglob(pattern)
        if file.is_file()
    }


def junit_costs(data: bytes, tests: set[str]) -> dict[str, Cost]:
    from scripts.ci_impact_results import file_cases

    if (
        len(data) > MAX_JUNIT_BYTES
        or b"\0" in data
        or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", data, re.I)
    ):
        raise ValueError("Unsupported JUnit report")
    seconds: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    skips: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for path, case in file_cases(BytesIO(data), tests):
        if path is None:
            raise ValueError("Unusable receipt: unmapped JUnit record")
        time = float(case.get("time", "nan"))
        if not math.isfinite(time) or not 0 <= time <= 86400:
            raise ValueError(f"Missing or invalid JUnit testcase time in {path}")
        if case.find("failure") is not None or case.find("error") is not None:
            raise ValueError(f"Unusable JUnit result for {path}")
        seconds[path] += time
        counts[path] += 1
        if case.find("skipped") is not None:
            skips[path].append((case.get("classname", ""), case.get("name", "")))
    return {
        path: Cost(
            round(elapsed, 3),
            counts[path],
            hashlib.sha256(json.dumps(sorted(skips[path])).encode()).hexdigest()
            if path in skips
            else "",
        )
        for path, elapsed in seconds.items()
    }


def full_scope(data: bytes) -> set[str]:
    scope = json.loads(data)
    if not isinstance(scope, dict) or scope.get("skipped") != {}:
        raise ValueError("Full nightly scope must not skip cached modules")
    tests = scope.get("ran")
    if (
        not isinstance(tests, list)
        or not tests
        or not all(isinstance(path, str) for path in tests)
    ):
        raise ValueError("Full nightly scope must list its test inputs")
    return set(tests)


def read_junit(paths: Iterable[Path], tests: set[str]) -> dict[str, Cost]:
    modules: dict[str, Cost] = {}
    for path in paths:
        with path.open("rb") as file:
            receipt = junit_costs(file.read(MAX_JUNIT_BYTES + 1), tests)
        if modules.keys() & receipt.keys():
            raise ValueError(f"Duplicate test modules across JUnit reports: {path}")
        modules.update(receipt)
    return modules


def require_unsharded(shard_count: bytes) -> None:
    if shard_count.strip() != b"1":
        raise ValueError("The budget requires an unsharded nightly artifact")


def nightly_costs(report: bytes, archived_scope: bytes) -> dict[str, Cost]:
    scope = full_scope(archived_scope)
    modules = junit_costs(report, scope)
    missing = scope - modules.keys() - REMOTE_ONLY
    if missing:
        raise ValueError(
            f"Nightly JUnit is missing scoped test modules: {sorted(missing)}"
        )
    return modules


def make_baseline(run: int, modules: dict[str, Cost]) -> Baseline:
    if not modules:
        raise ValueError("Calibration needs nonempty full nightly JUnit")
    targets = {
        key: Cost(
            math.ceil((cost.seconds + max(cost.seconds * 0.15, 10)) / 5) * 5,
            math.ceil(cost.cases * 1.10 / 5) * 5,
        )
        for key, cost in totals(modules).items()
    }
    return Baseline(run, modules, targets)


def load_baseline(path: Path = BASELINE) -> Baseline:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Baseline(
        int(data["run"]),
        {name: Cost(*values) for name, values in data["modules"].items()},
        {name: Cost(*values) for name, values in data["targets"].items()},
    )


def write_baseline(baseline: Baseline, path: Path) -> None:
    # Keep one module per line so a recalibration is reviewable.
    lines = []
    for label, costs in (("targets", baseline.targets), ("modules", baseline.modules)):
        rows = []
        for name, cost in sorted(costs.items()):
            values: list[float | str] = [cost.seconds, cost.cases]
            if cost.skip_signature:
                values.append(cost.skip_signature)
            rows.append(f"    {json.dumps(name)}: {json.dumps(values)}")
        lines.append(f'  "{label}": {{\n' + ",\n".join(rows) + "\n  }")
    path.write_text(
        f'{{\n  "run": {baseline.run},\n' + ",\n".join(lines) + "\n}\n",
        encoding="utf-8",
    )


def ready_summary(
    baseline: Baseline, observed: dict[str, Cost], tests: set[str]
) -> str:
    title = "### Ubuntu test budget (advisory)\n"
    if not observed:
        return title + "\nNo regular pytest cases were measured in this ready run.\n"
    original = totals(baseline.modules)
    projected = {path: cost for path, cost in baseline.modules.items() if path in tests}
    withheld = 0
    coverage_changed: dict[str, Cost] = {}
    for path, cost in observed.items():
        prior = projected.get(path)
        if prior is not None and cost.skip_signature != prior.skip_signature:
            coverage_changed[path] = prior
            if cost.seconds < prior.seconds:
                withheld += 1
                cost = Cost(prior.seconds, cost.cases)
        projected[path] = cost
    estimate = totals(projected)
    comparable = totals(projected | coverage_changed)
    suite_grew = round(estimate[TOTAL].seconds - original[TOTAL].seconds, 3) > 0
    comparable_growth = (
        round(comparable[TOTAL].seconds - original[TOTAL].seconds, 3) > 0
    )
    lines = [
        title,
        f"Measured {len(observed)} modules; carrying forward {len(projected.keys() - observed.keys())} "
        f"unmeasured modules from Ubuntu nightly {baseline.run}. "
        f"Withheld speedup credit for {withheld} modules with changed skips; "
        f"candidate signals exclude {len(coverage_changed)} such modules' timing changes.",
        "Summed pytest case time, not runner wall time. Setup, instrumentation and worker "
        "differences can affect this projection. Changes are against the pinned nightly, "
        "not the PR base.",
        "",
        "| Area | Projected seconds / budget | Δ seconds | Cases / target | Signal |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for name, target in sorted(baseline.targets.items()):
        cost = estimate.get(name, Cost())
        delta = round(cost.seconds - original.get(name, Cost()).seconds, 3)
        comparable_cost = comparable.get(name, Cost()).seconds
        signal = []
        if cost.seconds > target.seconds:
            if delta > 0 and not suite_grew:
                signal.append("suite offset")
            elif (
                delta > 0
                and comparable_growth
                and comparable_cost > original.get(name, Cost()).seconds
                and comparable_cost > target.seconds * (1 + EXCESS)
            ):
                signal.append("candidate regression")
            else:
                signal.append("over runtime budget")
        if cost.cases > target.cases:
            signal.append("over count target")
        lines.append(
            f"| {name} | {cost.seconds:.1f} / {target.seconds:g} | {delta:+.1f} | "
            f"{cost.cases} / {target.cases} | {', '.join(signal) or '—'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, action="append", default=[])
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument(
        "--nightly-artifact",
        type=Path,
        help="Extracted tests-ubuntu-all artifact directory",
    )
    parser.add_argument(
        "--calibrate-run",
        type=int,
        help="Replace --baseline using JUnit from this successful full Ubuntu nightly",
    )
    args = parser.parse_args(argv)
    if args.calibrate_run is not None:
        if args.nightly_artifact is None or args.junit:
            parser.error("--calibrate-run requires --nightly-artifact, not --junit")
        artifact = args.nightly_artifact
        shard = artifact / "shard-count"
        try:
            require_unsharded(shard.read_bytes() if shard.is_file() else b"1")
        except ValueError as error:
            parser.error(str(error))
        with (artifact / "pytest.xml").open("rb") as report:
            modules = nightly_costs(
                report.read(MAX_JUNIT_BYTES + 1),
                (artifact / "test-scope.json").read_bytes(),
            )
        write_baseline(make_baseline(args.calibrate_run, modules), args.baseline)
    else:
        from scripts.ci_test_dependencies import ROOT

        files = test_files(ROOT)
        print(
            ready_summary(
                load_baseline(args.baseline), read_junit(args.junit, files), files
            ),
            end="",
        )


if __name__ == "__main__":
    main()
