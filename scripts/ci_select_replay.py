"""Replay recent merged PR files and inspect failed Actions logs via gh."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from statistics import mean
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_select_tests import select_tests  # noqa: E402
from scripts.ci_test_dependencies import ROOT  # noqa: E402

FAILED_TEST = re.compile(r"\b(?:FAILED|ERROR)\s+(tests/[^\s:]+\.py)(?=::|\s|$)")


def failed_test_files(log: str) -> set[str]:
    return {match.group(1) for match in FAILED_TEST.finditer(log)}


def gh_json(*args: str) -> Any:
    return json.loads(subprocess.check_output(["gh", *args], text=True))


def replay(
    repository: str, limit: int, output: Path, baseline_minutes: float
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    prs = gh_json(
        "pr",
        "list",
        "--repo",
        repository,
        "--state",
        "merged",
        "--limit",
        str(limit * 2),
        "--json",
        "number,mergedAt,headRefOid,mergeCommit",
    )
    prs = sorted(prs, key=lambda pr: pr["mergedAt"], reverse=True)[:limit]
    all_tests = {
        file.relative_to(ROOT).as_posix()
        for file in (ROOT / "tests").rglob("test_*.py")
    }
    records = []
    for pr in prs:
        number = pr["number"]
        pages = gh_json(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/pulls/{number}/files?per_page=100",
        )
        paths = sorted(
            {
                path
                for page in pages
                for file in page
                for path in [
                    file["filename"],
                    *(
                        [file["previous_filename"]]
                        if "previous_filename" in file
                        else []
                    ),
                ]
            }
        )
        selection = select_tests(paths)
        selected = all_tests if selection == "all" else set(selection)
        runs = gh_json(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/actions/runs?head_sha={pr['headRefOid']}&per_page=100",
        )
        failures = [
            run
            for page in runs
            for run in page["workflow_runs"]
            if run["conclusion"] == "failure"
        ]
        failed_tests: set[str] = set()
        unavailable = []
        for run in failures:
            result = subprocess.run(
                [
                    "gh",
                    "run",
                    "view",
                    str(run["id"]),
                    "--repo",
                    repository,
                    "--log-failed",
                ],
                text=True,
                capture_output=True,
            )
            (output / f"pr-{number}-run-{run['id']}.log").write_text(
                result.stdout + result.stderr,
                encoding="utf-8",
            )
            if result.returncode:
                unavailable.append(run["id"])
            failed_tests.update(failed_test_files(result.stdout))
        records.append(
            {
                "pr": number,
                "merged_at": pr["mergedAt"],
                "head": pr["headRefOid"],
                "paths": paths,
                "selection": selection,
                "selected_percent": 100 * len(selected) / len(all_tests),
                "estimated_minutes_saved": baseline_minutes
                * (1 - len(selected) / len(all_tests)),
                "failed_runs_checked": [run["id"] for run in failures],
                "unavailable_logs": unavailable,
                "failed_test_files": sorted(failed_tests),
                "misses": sorted(failed_tests - selected),
            }
        )
        (output / "replay.json").write_text(
            json.dumps(records, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"PR {number}: {len(selected)}/{len(all_tests)}, {len(failures)} failed runs",
            flush=True,
        )
    return summarize(records, output, baseline_minutes, len(all_tests))


def summarize(
    records: list[dict[str, Any]], output: Path, baseline_minutes: float, count: int
) -> dict[str, Any]:
    summary = {
        "prs": len(records),
        "test_files": count,
        "mean_selected_percent": mean(record["selected_percent"] for record in records),
        "estimated_runner_minutes_saved": sum(
            record["estimated_minutes_saved"] for record in records
        ),
        "baseline_runner_minutes_per_full_suite": baseline_minutes,
        "estimate_method": "Linear test-file fraction against documented pre-routing 125-minute matrix; excludes setup/fixed jobs. Not measured billing savings.",
        "failed_runs_checked": sum(
            len(record["failed_runs_checked"]) for record in records
        ),
        "unavailable_logs": sum(len(record["unavailable_logs"]) for record in records),
        "failed_test_files_observed": sum(
            len(record["failed_test_files"]) for record in records
        ),
        "misses": [
            {"pr": record["pr"], "tests": record["misses"]}
            for record in records
            if record["misses"]
        ],
        "limitations": "Current checkout graph, not historical source graphs; historical deleted/unknown modules fail open. Failed logs only, not complete JUnit coverage. No failed runs or missing logs is not proof of no misses.",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def replay_cached(output: Path, baseline_minutes: float) -> dict[str, Any]:
    """Re-evaluate saved PR files/failures without repeating GitHub downloads."""
    records = json.loads((output / "replay.json").read_text(encoding="utf-8"))
    all_tests = {
        file.relative_to(ROOT).as_posix()
        for file in (ROOT / "tests").rglob("test_*.py")
    }
    for record in records:
        selection = select_tests(record["paths"])
        selected = all_tests if selection == "all" else set(selection)
        record.update(
            selection=selection,
            selected_percent=100 * len(selected) / len(all_tests),
            estimated_minutes_saved=baseline_minutes
            * (1 - len(selected) / len(all_tests)),
            misses=sorted(set(record["failed_test_files"]) - selected),
        )
    (output / "replay.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    return summarize(records, output, baseline_minutes, len(all_tests))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="WGSExtract/wgsextract-cli")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", type=Path, default=Path("out/914"))
    parser.add_argument("--baseline-minutes", type=float, default=125)
    parser.add_argument("--reuse-evidence", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            replay_cached(args.output, args.baseline_minutes)
            if args.reuse_evidence
            else replay(args.repo, args.limit, args.output, args.baseline_minutes),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
