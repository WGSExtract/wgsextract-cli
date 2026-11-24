"""Plan hosted pytest jobs from trusted, full main-branch JUnit artifacts."""

from __future__ import annotations

import argparse
import ast
import io
import json
import math
import statistics
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TypedDict
from urllib.parse import urlencode

from scripts.ci_impact_results import file_results
from scripts.ci_run_tests import test_targets
from scripts.ci_test_tiers import SINGLE_OS_MODULES, module_shard, shared_fake_data

MAX_JUNIT_BYTES = 16 * 1024 * 1024
# Reserve 35 s for selector Pixi/setup/fetch and 40 s for the receipt job.
UBUNTU_PR_SHARD_OVERHEAD = 75


@dataclass(frozen=True)
class Lane:
    target: int
    effective_workers: float
    slots: int
    max_jobs: int
    lead_setup: int
    repeated_setup: int


# Calibrated against successful full scheduled jobs. macOS has five slots.
LANES = {
    "ubuntu": Lane(12 * 60, 3.7, 4, 3, 161, 62),
    "windows": Lane(15 * 60, 1.9, 4, 3, 239, 73),
    "macos": Lane(22 * 60, 2.9, 3, 2, 290, 125),
}


class Shard(TypedDict):
    label: str
    selection: str
    seconds: int
    gate: str


def serial(seconds: int = 0, selection: str = "", gate: str = "") -> list[Shard]:
    return [{"label": "all", "selection": selection, "seconds": seconds, "gate": gate}]


def suite_files() -> list[str]:
    return (
        test_targets(
            json.dumps(
                sorted(
                    {
                        path.as_posix()
                        for pattern in ("test_*.py", "*_test.py")
                        for path in Path("tests").rglob(pattern)
                        if path.is_file()
                    }
                )
            )
        )
        or []
    )


def junit_seconds(junit: Path | IO[bytes], files: set[str]) -> dict[str, float]:
    results, _ = file_results(junit, files)
    return {
        path: result["seconds"]
        for path, result in results.items()
        if math.isfinite(result["seconds"]) and result["seconds"] >= 0
    }


def fixture_sets(targets: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    tagged: list[str] = []
    for path in targets:
        try:
            source = Path(path).read_bytes()
            marked = b"xdist_group" in source and any(
                isinstance(node, ast.Attribute) and node.attr == "xdist_group"
                for node in ast.walk(ast.parse(source, path))
            )
        except (OSError, SyntaxError):
            # Keep unreadable modules in the plan; pytest owns collection errors.
            marked = True
        if marked:
            tagged.append(path)
        else:
            group = "shared-smoke" if shared_fake_data(Path(path)) else path
            grouped[group].append(path)
    if tagged:
        # A static plan cannot evaluate dynamic marker names. Keep marked
        # files on one runner, including any connected shared-smoke fixtures.
        if any(shared_fake_data(Path(path)) for path in tagged):
            tagged.extend(grouped.pop("shared-smoke", []))
        grouped["xdist-marked"] = tagged
    return grouped


@dataclass(frozen=True)
class Group:
    key: str
    paths: list[str]
    seconds: float
    known: bool
    lead: bool


def pack(groups: list[Group], count: int) -> list[list[Group]]:
    bins: list[list[Group]] = [[] for _ in range(count)]
    totals = [0.0] * count
    known = sorted(
        (group for group in groups if group.known and not group.lead),
        key=lambda group: (-group.seconds, group.key),
    )
    for group in [
        *(group for group in groups if group.lead),
        *(group for group in groups if not group.known and not group.lead),
        *known,
    ]:
        if group.lead:
            target = 0
        elif group.known:
            target = min(
                range(count), key=lambda index: (totals[index], len(bins[index]), index)
            )
        else:
            target = module_shard(group.key, count)
        bins[target].append(group)
        totals[target] += group.seconds
    return bins


def worker_wall(groups: list[Group], lane: Lane) -> float:
    """Estimate completion without spreading one fixture group over workers."""
    finish = [0.0] * lane.slots
    weights = sorted((group.seconds for group in groups), reverse=True)
    for seconds in weights:
        worker = min(range(lane.slots), key=finish.__getitem__)
        finish[worker] += seconds
    return max(max(finish), sum(weights) / lane.effective_workers)


def plan(
    os: str,
    selection: str,
    durations: Mapping[str, float],
    *,
    skip_independent: bool,
    gate: str = "",
    pull_request: bool = False,
) -> list[Shard]:
    if not durations or os not in LANES:
        return serial()
    targets = test_targets(selection)
    if targets is None:
        targets = suite_files()
    targets = sorted(
        path
        for path in set(targets)
        if not skip_independent or Path(path).name not in SINGLE_OS_MODULES
    )
    median = max(1.0, statistics.median(durations.values()))
    groups = [
        Group(
            group,
            paths,
            sum(durations.get(path, median) for path in paths),
            any(path in durations for path in paths),
            os in {"windows", "macos"} and "tests/test_imputation.py" in paths,
        )
        for group, paths in fixture_sets(targets).items()
    ]
    lane = LANES[os]
    wall = worker_wall(groups, lane)
    baseline = wall + lane.lead_setup
    overhead = UBUNTU_PR_SHARD_OVERHEAD if os == "ubuntu" and pull_request else 0
    desired = min(
        lane.max_jobs,
        len(groups),
        math.ceil(wall / lane.target),
    )
    for count in range(desired, 1, -1):
        bins = pack(groups, count)
        times = [worker_wall(shard, lane) for shard in bins]
        jobs = [
            seconds + (lane.repeated_setup if index else lane.lead_setup)
            for index, seconds in enumerate(times)
        ]
        if (
            all(bins)
            and max(times) <= 1.2 * sum(times) / count
            and max(jobs) + overhead < baseline
            and sum(jobs) + overhead <= 1.05 * baseline
        ):
            return [
                {
                    "label": "all" if index == 0 else f"shard-{index + 1}",
                    "selection": json.dumps(
                        sorted(path for group in shard for path in group.paths),
                        separators=(",", ":"),
                    ),
                    "seconds": round(times[index]),
                    "gate": gate,
                }
                for index, shard in enumerate(bins)
            ]
    return serial(round(wall), selection if gate else "", gate)


Api = Callable[[str], bytes]


def gh_api(path: str) -> bytes:
    return subprocess.run(
        ["gh", "api", path], capture_output=True, check=True, timeout=15
    ).stdout


def artifact_durations(evidence: dict[str, bytes], files: set[str]) -> dict[str, float]:
    durations: dict[str, float] = {}
    for label, archive in evidence.items():
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            count = (
                int(zipped.read("shard-count"))
                if "shard-count" in zipped.namelist()
                else 1
            )
            if count not in {1, 2, 3}:
                raise ValueError(f"{label} declared an unsupported shard count")
            labels = {"all", *(f"shard-{index}" for index in range(2, count + 1))}
            if labels != evidence.keys():
                raise ValueError(f"{label} belongs to an incomplete timing run")
            report = zipped.getinfo("pytest.xml")
            if report.file_size > MAX_JUNIT_BYTES:
                raise ValueError("JUnit report is too large")
            with zipped.open(report) as junit:
                part = junit_seconds(junit, files)
            if durations.keys() & part.keys():
                raise ValueError("A module occurred in multiple JUnit shards")
            durations.update(part)
    return durations


def trusted_durations(
    repository: str, os: str, *, api: Api = gh_api
) -> dict[str, float]:
    """Only read completed schedules from this repository's exact main branch."""
    root = f"repos/{repository}/actions"
    queries = urlencode(
        {"event": "schedule", "branch": "main", "status": "success", "per_page": 14}
    )
    runs = json.loads(api(f"{root}/workflows/{os}_ci.yml/runs?{queries}"))[
        "workflow_runs"
    ]
    prefix = f"tests-{os}-"
    files = set(suite_files())
    for run in runs:
        if not (
            run["event"] == "schedule"
            and run["head_branch"] == "main"
            and run["conclusion"] == "success"
            and (run["head_repository"] or {}).get("full_name") == repository
        ):
            continue
        artifacts = json.loads(
            api(f"{root}/runs/{int(run['id'])}/artifacts?per_page=100")
        )["artifacts"]
        reports = [
            item
            for item in artifacts
            if item["name"].startswith(prefix)
            and not item["expired"]
            and (item.get("workflow_run") or {}).get("head_sha") == run["head_sha"]
        ]
        if not any(item["name"] == prefix + "all" for item in reports):
            continue
        try:
            archives = {
                item["name"].removeprefix(prefix): api(
                    f"{root}/artifacts/{int(item['id'])}/zip"
                )
                for item in reports
            }
            durations = artifact_durations(archives, files)
        except (ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as error:
            print(f"{os}: ignored unusable JUnit from run {run['id']} ({error!r}).")
            continue
        if durations:
            print(f"{os}: timing estimates from scheduled main run {run['id']}.")
            return durations
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--os", choices=LANES, required=True)
    parser.add_argument("--selection", default="all")
    parser.add_argument("--skip-independent", action="store_true")
    parser.add_argument("--pull-request", action="store_true")
    parser.add_argument("--linux-observations", type=Path)
    parser.add_argument("--disable-symbol-selection", action="store_true")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--repository")
    source.add_argument("--junit", type=Path)
    source.add_argument("--timings", type=Path)
    parser.add_argument("--save-timings", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--output-name", default="matrix", choices=("matrix", *LANES))
    args = parser.parse_args()
    try:
        if args.junit:
            durations = junit_seconds(args.junit, set(suite_files()))
        elif args.timings:
            durations = json.loads(args.timings.read_text(encoding="utf-8"))
        else:
            durations = trusted_durations(args.repository, args.os)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ET.ParseError,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
    ) as error:
        print(f"{args.os}: duration evidence unavailable ({error!r}); using one job.")
        durations = {}
    if args.save_timings:
        args.save_timings.parent.mkdir(parents=True, exist_ok=True)
        args.save_timings.write_text(json.dumps(durations) + "\n", encoding="utf-8")
    shards = plan(
        args.os,
        args.selection,
        durations,
        skip_independent=args.skip_independent,
        pull_request=args.pull_request,
    )
    if args.os == "ubuntu" and len(shards) > 1 and args.linux_observations:
        # A single decision must precede partitioning. Re-running the symbol
        # gate independently in each job could yield inconsistent coverage.
        from scripts.ci_impact_gate import plan as linux_selection

        try:
            decision = linux_selection(
                Path.cwd().resolve(),
                event="pull_request",
                base="HEAD^1",
                observations=args.linux_observations,
                legacy=args.selection,
                disabled=args.disable_symbol_selection,
            )
            selection = decision["selection"]
            shards = plan(
                args.os,
                selection if isinstance(selection, str) else json.dumps(selection),
                durations,
                skip_independent=args.skip_independent,
                gate=decision["mode"],
                pull_request=args.pull_request,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"Linux selector unavailable ({error!r}); using one job.")
            shards = serial()
    matrix = json.dumps(shards, separators=(",", ":"))
    if args.github_output is None:
        print(matrix)
    else:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"{args.output_name}={matrix}\n")


if __name__ == "__main__":
    main()
