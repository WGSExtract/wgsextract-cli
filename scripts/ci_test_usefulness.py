"""Summarize weekly per-test evidence and find advisory review candidates."""

from __future__ import annotations

import ast
import re
import statistics
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from itertools import combinations
from pathlib import Path
from typing import Any

from scripts.ci_test_usefulness_inputs import PREMERGE_PER_LANE, Evidence, Report

MIN_PREMERGE_PASSES = 15
MIN_PREMERGE_RUNS = 5
MIN_PREMERGE_SECONDS = 120
MIN_SHARED_PASSES = 5
LIMIT = 12
TITLE = "Weekly test-usefulness review"
MARKER = "<!-- weekly-test-usefulness:v1 -->"


@dataclass(frozen=True)
class Change:
    sha: str
    date: str
    path: str


@dataclass
class TestStats:
    outcomes: Counter[str] = field(default_factory=Counter)
    timings: list[float] = field(default_factory=list)
    premerge_passes: Counter[tuple[str, str]] = field(default_factory=Counter)
    premerge_runs: dict[tuple[str, str], set[int]] = field(
        default_factory=lambda: defaultdict(set)
    )
    premerge_seconds: dict[tuple[str, str], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    nightly: set[tuple[str, str]] = field(default_factory=set)
    passing_reports: set[tuple[int, str, str, str]] = field(default_factory=set)
    flakes: list[tuple[int, int]] = field(default_factory=list)
    same_pr_head: list[tuple[int, int]] = field(default_factory=list)

    @property
    def nightly_savings(self) -> float:
        return sum(self.premerge_seconds.get(lane, 0) for lane in self.nightly)

    @property
    def move(self) -> bool:
        return (
            self.outcomes["fail"] == 0
            and sum(self.premerge_passes[lane] for lane in self.nightly)
            >= MIN_PREMERGE_PASSES
            and len(set().union(*(self.premerge_runs[lane] for lane in self.nightly)))
            >= MIN_PREMERGE_RUNS
            and self.nightly_savings >= MIN_PREMERGE_SECONDS
        )


def summarize(reports: list[Report]) -> dict[str, TestStats]:
    tests: dict[str, TestStats] = defaultdict(TestStats)
    later_pass: dict[tuple[str, str, str, str, str], Report] = {}
    for report in sorted(reports, key=lambda x: (x.at, x.artifact), reverse=True):
        lane = (report.os, report.suite)
        for case in report.cases:
            row = tests[case.node]
            row.outcomes[case.result] += 1
            if case.result == "skip":
                continue
            row.timings.append(case.seconds)
            if report.stage != "nightly":
                row.premerge_seconds[lane] += case.seconds
            key = (case.node, report.sha, report.stage, *lane)
            if case.result == "fail":
                if (
                    passed := later_pass.get(key)
                ) and passed.artifact != report.artifact:
                    signals = row.same_pr_head if report.stage == "PR" else row.flakes
                    signals.append((report.run, passed.run))
                continue
            later_pass[key] = report
            row.passing_reports.add((report.run, report.stage, *lane))
            if report.stage == "nightly":
                row.nightly.add(lane)
            else:
                row.premerge_passes[lane] += 1
                row.premerge_runs[lane].add(report.run)
    return dict(tests)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True)


def source_changes(root: Path, sources: set[str]) -> dict[str, Change]:
    """Dates when sources last changed on the reporting branch's first-parent history."""
    if not sources:
        return {}
    tracked = set(git(root, "ls-files", "-z", "--", "src/wgsextract_cli").split("\0"))
    remaining = sources & tracked
    changes = {}
    sha, date = "", ""
    for line in git(
        root,
        "log",
        "--first-parent",
        "--diff-merges=first-parent",
        "--no-renames",
        "--format=@%H %cs",
        "--name-only",
        "HEAD",
        "--",
        "src/wgsextract_cli",
    ).splitlines():
        if line.startswith("@"):
            sha, date = line[1:].split()
        elif line in remaining:
            changes[line] = Change(sha, date, line)
            remaining.remove(line)
            if not remaining:
                break
    return changes


class MaskLiterals(ast.NodeTransformer):
    def __init__(self) -> None:
        self.values: list[str] = []

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        self.values.append(ast.dump(node))
        return ast.Constant(type(node.value).__name__)


def signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, str, list[str]]:
    body = node.body[1:] if ast.get_docstring(node) is not None else node.body
    context = repr(
        (
            type(node).__name__,
            ast.dump(node.args),
            [ast.dump(d) for d in node.decorator_list],
            ast.dump(node.returns) if node.returns else None,
            node.type_comment,
            [ast.dump(p) for p in getattr(node, "type_params", [])],
        )
    )
    mask = MaskLiterals()
    shape = ast.dump(mask.visit(ast.Module(body=body, type_ignores=[])))
    return context, shape, mask.values


def definitions(
    root: Path, file: str
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse((root / file).read_bytes())
    return {
        "::".join([file, *parents, fn.name]): fn
        for parents, scope in [
            ([], tree.body),
            *(
                ([node.name], node.body)
                for node in tree.body
                if isinstance(node, ast.ClassDef)
            ),
        ]
        for fn in scope
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and fn.name.startswith("test_")
    }


def current_moves(root: Path, tests: dict[str, TestStats]) -> set[str]:
    candidates: dict[str, list[str]] = defaultdict(list)
    for node, row in tests.items():
        if row.move:
            candidates[node.partition("::")[0]].append(node)
    moves: set[str] = set()
    for file, nodes in candidates.items():
        names = definitions(root, file)
        moves.update(node for node in nodes if node.split("[", 1)[0] in names)
    return moves


@dataclass(frozen=True)
class Pair:
    left: str
    right: str
    shared_passes: int
    exact: bool


def duplicates(root: Path, tests: dict[str, TestStats]) -> list[Pair]:
    files = {
        node.split("::", 1)[0]
        for node, row in tests.items()
        if row.outcomes["pass"] >= MIN_SHARED_PASSES and not row.outcomes["fail"]
    }
    pairs = []
    for file in sorted(files):
        similar: dict[tuple[str, str, str], list[tuple[str, list[str]]]] = defaultdict(
            list
        )
        for node, fn in sorted(definitions(root, file).items()):
            row = tests.get(node)
            if (
                row is None
                or row.outcomes["fail"]
                or len(row.passing_reports) < MIN_SHARED_PASSES
            ):
                continue
            context, shape, literals = signature(fn)
            similar[node.rsplit("::", 1)[0], context, shape].append((node, literals))
        for group in similar.values():
            for (left, first), (right, second) in combinations(group, 2):
                edits = sum(a != b for a, b in zip(first, second, strict=True))
                if edits > 1:
                    continue
                shared = len(
                    {
                        run
                        for run, *_ in tests[left].passing_reports
                        & tests[right].passing_reports
                    }
                )
                if shared >= MIN_SHARED_PASSES:
                    pairs.append(Pair(left, right, shared, edits == 0))
    return sorted(pairs, key=lambda pair: (-pair.shared_passes, pair.left, pair.right))


def inline(text: str) -> str:
    # Keep artifact-supplied parameter IDs inert and on a single Markdown line.
    safe = re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", " ", text)
    return f"<code>{escape(safe).replace('`', '&#96;').replace('|', '&#124;')}</code>"


def data_rows(tests: dict[str, TestStats]) -> list[dict[str, Any]]:
    return [
        {
            "test": node,
            "passes": row.outcomes["pass"],
            "failures": row.outcomes["fail"],
            "skips": row.outcomes["skip"],
            "seconds": round(sum(row.timings), 3),
            "median_seconds": round(statistics.median(row.timings), 3)
            if row.timings
            else None,
            "premerge_seconds": round(sum(row.premerge_seconds.values()), 3),
            "nightly_overlap_seconds": round(row.nightly_savings, 3),
            "same_sha_failure_then_pass_runs": row.flakes,
            "same_pr_head_failure_then_pass_runs": row.same_pr_head,
        }
        for node, row in sorted(
            tests.items(), key=lambda item: (-sum(item[1].timings), item[0])
        )
    ]


def render(
    repository: str,
    run_id: int,
    start: datetime,
    end: datetime,
    evidence: Evidence,
    tests: dict[str, TestStats],
    changes: dict[str, Change],
    pairs: list[Pair],
    moves: set[str],
) -> str:
    reports = evidence.reports
    coverage = evidence.coverage
    link = f"https://github.com/{repository}"
    lines = [
        MARKER,
        f"(AI): {TITLE}",
        "",
        f"UTC window: **{start:%Y-%m-%d}** to **{end:%Y-%m-%d}** (exclusive). "
        f"{len({r.artifact for r in reports}):,} JUnit artifacts from "
        f"{len({r.run for r in reports}):,} runs; **{len(tests):,}** observed test cases. "
        f"{evidence.expired:,} matching artifacts expired, {evidence.without_junit:,} with no "
        f"recognized JUnit file, {evidence.unmapped:,} records did not map to current test IDs. "
        f"{evidence.sampled_out:,} pre-merge artifacts left out of the sample "
        f"(up to {PREMERGE_PER_LANE} per OS and stage, with priority for failed runs and "
        "same-SHA comparisons). Counts describe this sample, not the full suite.",
        "",
        f"[Download full per-test timings, outcomes and coverage dates]({link}/actions/runs/{run_id}) "
        "from the run's `weekly-test-usefulness` artifact. Times are summed JUnit test "
        "seconds, not runner wall time. Missing reports and skips are not passes; "
        "historical artifacts may have been overwritten by reruns.",
        "",
        f"Last code change uses the newest changed source file observed by the "
        f"**test module**, not the individual case, in "
        f"{f'the [Linux nightly coverage map]({link}/commit/{evidence.map_sha})' if evidence.map_sha else 'a Linux nightly coverage map (unavailable this week)'}. "
        "Other platforms and unobserved code may differ.",
        "",
    ]

    def last_change(node: str) -> str:
        files = coverage.get(node.split("::", 1)[0], set())
        latest = max(
            (changes[path] for path in files if path in changes),
            key=lambda row: (row.date, row.path),
            default=None,
        )
        return (
            f"[{latest.date}]({link}/commit/{latest.sha}) ({inline(latest.path)})"
            if latest
            else "unknown"
        )

    def section(title: str, description: str, rows: list[str]) -> None:
        lines.extend(
            [
                f"### {title}",
                "",
                description,
                "",
                *(rows[:LIMIT] or ["None met the criteria."]),
                "",
            ]
        )

    by_cost = sorted(tests, key=lambda node: (-sum(tests[node].timings), node))
    section(
        "Move to nightly",
        (
            f"No recorded failures, ≥{MIN_PREMERGE_PASSES} pre-merge passes across "
            f"≥{MIN_PREMERGE_RUNS} runs and ≥{MIN_PREMERGE_SECONDS} pre-merge test seconds "
            "on OS/suite combinations where this same case also passed at night. "
            "The test function still exists. Verify current parameter IDs and PR gate coverage before moving."
        ),
        [
            f"- {inline(node)}: **{row.nightly_savings:.0f}s** pre-merge overlap "
            f"({sum(row.premerge_passes[lane] for lane in row.nightly)} passes; "
            f"{', '.join(sorted({os for os, _ in row.nightly & row.premerge_passes.keys()}))}); "
            f"code last changed: {last_change(node)}."
            for node, row in sorted(
                tests.items(), key=lambda item: (-item[1].nightly_savings, item[0])
            )
            if node in moves
        ],
    )
    section(
        "Investigate possible flakes",
        (
            "A failure followed by a pass on the same SHA, OS, stage and suite is a "
            "flake signal, not proof of a test defect. Check for runner/tool failures first."
        ),
        [
            f"- {inline(node)}: {len(row.flakes)} of {row.outcomes['fail']} failures followed by "
            f"a same-SHA pass ([failure]({link}/actions/runs/{row.flakes[0][0]}), "
            f"[pass]({link}/actions/runs/{row.flakes[0][1]})); code last changed: {last_change(node)}."
            for node, row in sorted(
                tests.items(), key=lambda item: (-len(item[1].flakes), item[0])
            )
            if row.flakes
        ],
    )
    section(
        "Check inconsistent PR results",
        "The PR head SHA, OS and suite match, but the tested merge tree can change "
        "when the base advances. Verify the base before treating these as flakes.",
        [
            f"- {inline(node)}: {len(row.same_pr_head)} of {row.outcomes['fail']} failures followed by "
            f"a pass at the same PR head ([failure]({link}/actions/runs/{row.same_pr_head[0][0]}), "
            f"[pass]({link}/actions/runs/{row.same_pr_head[0][1]})); code last changed: {last_change(node)}."
            for node, row in sorted(
                tests.items(), key=lambda item: (-len(item[1].same_pr_head), item[0])
            )
            if row.same_pr_head
        ],
    )
    for exact, action in [(False, "Merge"), (True, "Delete redundant case")]:
        section(
            action,
            (
                f"Current test bodies in the same scope {'match' if exact else 'differ by one literal'}, "
                f"with matching signatures/marks and ≥{MIN_SHARED_PASSES} shared passing runs. "
                "Verify intent and fixture behavior before changing either case."
            ),
            [
                f"- {'Keep ' if exact else 'Parametrize '}{inline(pair.left)} and "
                f"{'review removing ' if exact else ''}{inline(pair.right)} "
                f"({pair.shared_passes} shared passes); code last changed: {last_change(pair.left)}."
                for pair in pairs
                if pair.exact == exact
            ],
        )
    section(
        "Highest observed test cost",
        "Includes failures; skipped tests contribute no time.",
        [
            f"- {inline(node)}: {sum(tests[node].timings):.0f}s; "
            f"{tests[node].outcomes['pass']} pass / {tests[node].outcomes['fail']} fail / "
            f"{tests[node].outcomes['skip']} skip; code last changed: {last_change(node)}."
            for node in by_cost
            if tests[node].timings
        ],
    )
    return "\n".join(lines)
