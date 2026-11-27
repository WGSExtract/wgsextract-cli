"""Build the weekly test-usefulness report or publish its singleton issue."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.ci_efficiency_report import ReportGitHub
from scripts.ci_github import GitHub
from scripts.ci_merge_group_passes import gh_api
from scripts.ci_test_budget import TOTAL, Cost, load_baseline, totals
from scripts.ci_test_usefulness import (
    MARKER,
    TITLE,
    current_moves,
    data_rows,
    duplicates,
    git,
    render,
    source_changes,
    summarize,
)
from scripts.ci_test_usefulness_inputs import NightlyBudget, collect

BOT = "github-actions[bot]"


def budget_line(repository: str, reports: list[NightlyBudget]) -> str:
    nightly = max(
        reports,
        key=lambda report: (report.at, report.run),
        default=None,
    )
    if nightly is None:
        return "**Test budget:** No usable full Ubuntu nightly receipt in the window."
    measured = totals(nightly.modules)
    targets = load_baseline().targets
    full, budget = measured[TOTAL], targets[TOTAL]
    over = []
    for area, target in sorted(targets.items()):
        if area == TOTAL:
            continue
        cost = measured.get(area, Cost())
        limits = []
        if cost.seconds > target.seconds:
            limits.append(f"{cost.seconds:.0f}/{target.seconds:g}s")
        if cost.cases > target.cases:
            limits.append(f"{cost.cases:,}/{target.cases:,} cases")
        if limits:
            over.append(f"{area} ({', '.join(limits)})")
    return (
        f"**Test budget:** [Ubuntu nightly {nightly.run}]"
        f"(https://github.com/{repository}/actions/runs/{nightly.run}): "
        f"{full.seconds:,.0f}/{budget.seconds:,.0f}s and "
        f"{full.cases:,}/{budget.cases:,} cases; "
        + ("areas over target: " + "; ".join(over) if over else "no areas over target")
        + "."
    )


def publish(api: GitHub, body: str) -> int:
    if not body.startswith(MARKER + "\n") or len(body) > 65_536:
        raise ValueError("Invalid weekly review body")
    matches = [
        row
        for row in api.pages("issues?state=all&creator=github-actions%5Bbot%5D")
        if "pull_request" not in row
        and (row.get("user") or {}).get("login") == BOT
        and (
            row.get("title") == TITLE
            or (row.get("body") or "").startswith(MARKER + "\n")
        )
    ]
    if len(matches) > 1:
        raise ValueError("Multiple weekly test-usefulness issues; refusing to publish")
    if not matches:
        created = api.api(
            "POST",
            "issues",
            {
                "title": TITLE,
                "body": body,
                "labels": ["efficiency"],
            },
        )
        return int(created["number"])
    issue = matches[0]
    number = int(issue["number"])
    update = {}
    if issue["body"] != body:
        update["body"] = body
    if issue["state"] != "open":
        update["state"] = "open"
    if update:
        api.api("PATCH", f"issues/{number}", update)
    if not any(label["name"] == "efficiency" for label in issue["labels"]):
        api.api("POST", f"issues/{number}/labels", {"labels": ["efficiency"]})
    return number


def measure(
    api: GitHub,
    root: Path,
    now: datetime,
    run_id: int,
    output: Path,
    *,
    download: Callable[[str], bytes] = gh_api,
) -> str:
    midnight = now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = midnight - timedelta(days=midnight.weekday())
    start = end - timedelta(days=7)
    tests = {
        path
        for path in git(root, "ls-files", "-z", "--", "tests").split("\0")
        if Path(path).name.startswith("test_") and path.endswith(".py")
    }
    evidence = collect(api, start, end, now, tests, download)
    rows = summarize(evidence.reports)
    pairs = duplicates(root, rows)
    moves = current_moves(root, rows)
    coverage = evidence.coverage
    changes = source_changes(root, set().union(*coverage.values()))
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "coverage_map_sha": evidence.map_sha,
        "coverage_by_test_file": {
            test: {
                path: asdict(changes[path]) if path in changes else None
                for path in sorted(paths)
            }
            for test, paths in sorted(coverage.items())
            if test in tests
        },
        "junit_artifacts": len({report.artifact for report in evidence.reports}),
        "workflow_runs": len({report.run for report in evidence.reports}),
        "expired_artifacts": evidence.expired,
        "sampled_out_artifacts": evidence.sampled_out,
        "artifacts_without_junit": evidence.without_junit,
        "unmapped_junit_records": evidence.unmapped,
        "tests": data_rows(rows),
        "move_to_nightly": sorted(moves),
        "similar_tests": [asdict(pair) for pair in pairs],
    }
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return render(
        api.repository,
        run_id,
        start,
        end,
        evidence,
        rows,
        changes,
        pairs,
        moves,
        budget_line(api.repository, evidence.budgets),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser(
        "measure", help="Read GitHub artifacts and write the review locally"
    )
    read.add_argument("--run-id", type=int, required=True)
    read.add_argument("--output", type=Path, required=True)
    write = commands.add_parser("publish", help="Create or update the report issue")
    write.add_argument("--body", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "measure":
        print(
            measure(
                ReportGitHub(args.repository),
                Path.cwd(),
                datetime.now(timezone.utc),
                args.run_id,
                args.output,
            ),
            end="",
        )
    else:
        # Do not retry issue creation on an ambiguous transport failure.
        number = publish(GitHub(args.repository), args.body.read_text(encoding="utf-8"))
        print(f"Published https://github.com/{args.repository}/issues/{number}")


if __name__ == "__main__":
    main()
