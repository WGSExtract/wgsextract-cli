"""Read existing Actions artifacts; never extract archives or execute tests."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlencode

from scripts.ci_efficiency_report import Row, stamp
from scripts.ci_github import GitHub
from scripts.ci_merge_group_passes import gh_api
from scripts.ci_test_shards import LANES

MAX_BYTES = 16 * 1024 * 1024
MAX_OBSERVATION_BYTES = 64 * 1024 * 1024
PREMERGE_PER_LANE = 40
ARTIFACT_OS = {
    **{
        f"tests-{os}-{label}": os
        for os, lane in LANES.items()
        for label in [
            "all",
            *(f"shard-{index}" for index in range(2, lane.max_jobs + 1)),
        ]
    },
    "tests-linux-arm64-native": "linux-arm64",
    "tests-queue-ubuntu-latest": "ubuntu",
    "tests-queue-windows-latest": "windows",
}
JUNIT = {"pytest.xml", "imputation-e2e.xml", "vep-rs-e2e.xml", "e2e-fake.xml"}
SHAS = re.compile(r"[0-9a-f]{40}")
PR_WORKFLOW = ".github/workflows/pr_ci.yml"
OS_WORKFLOWS = {
    f".github/workflows/{file}.yml"
    for file in ("ubuntu_ci", "macos_ci", "windows_ci", "linux_arm64")
}
NODE_PART = re.compile(r"\w{1,160}", re.ASCII)
CASE_NAME = re.compile(r"(\w{1,160})(\[[^\r\n]{0,400}\])?", re.ASCII)
OUTCOME_PRIORITY = {"fail": 0, "skip": 1, "pass": 2}


@dataclass(frozen=True)
class Case:
    node: str
    result: str
    seconds: float


@dataclass(frozen=True)
class Report:
    artifact: int
    run: int
    sha: str
    stage: str
    os: str
    suite: str
    at: datetime
    cases: tuple[Case, ...]


@dataclass
class Evidence:
    reports: list[Report] = field(default_factory=list)
    map_sha: str | None = None
    coverage: dict[str, set[str]] = field(default_factory=dict)
    expired: int = 0
    sampled_out: int = 0
    without_junit: int = 0
    unmapped: int = 0


def duration(case: ET.Element) -> float:
    try:
        seconds = float(case.get("time", "0"))
    except ValueError:
        return 0.0
    return seconds if math.isfinite(seconds) and 0 <= seconds <= 86400 else 0.0


def junit(data: bytes, tests: set[str]) -> tuple[tuple[Case, ...], int]:
    if b"\0" in data or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", data, re.I):
        raise ValueError("Unsupported JUnit XML encoding or document type")
    modules = {path.removesuffix(".py").replace("/", "."): path for path in tests}
    cases: dict[str, Case] = {}
    unmapped = 0
    for case in ET.fromstring(data).iter("testcase"):
        klass = case.get("classname", "")
        if ".tests." in klass:
            klass = "tests." + klass.split(".tests.", 1)[1]
        classes: list[str] = []
        while klass and klass not in modules and f"tests.{klass}" not in modules:
            klass, _, part = klass.rpartition(".")
            classes.insert(0, part)
        path = modules.get(klass) or modules.get(f"tests.{klass}")
        if not path and case.get("file", "") in tests:
            path, classes = case.get("file"), []
        name = case.get("name", "")
        if (
            not path
            or not CASE_NAME.fullmatch(name)
            or not all(NODE_PART.fullmatch(part) for part in classes)
        ):
            unmapped += 1
            continue
        node = "::".join([path, *classes, name])
        failed = case.find("failure") is not None or case.find("error") is not None
        result = (
            "fail"
            if failed
            else ("skip" if case.find("skipped") is not None else "pass")
        )
        previous = cases.get(node)
        if previous:
            # Pytest can emit separate call and teardown records for one test.
            result = min(result, previous.result, key=OUTCOME_PRIORITY.__getitem__)
        cases[node] = Case(
            node, result, duration(case) + (previous.seconds if previous else 0)
        )
    return tuple(cases.values()), unmapped


def artifact_rows(api: GitHub, start: datetime, end: datetime) -> list[Row]:
    # Artifact IDs and creation timestamps are not in the same order.
    seen = {
        row["id"]: row
        for name in ARTIFACT_OS
        for row in api.pages(f"actions/artifacts?name={name}", "artifacts")
        if row["name"] == name and start <= stamp(row["created_at"]) < end
    }
    return list(seen.values())


def artifact_origin(artifact: Row) -> tuple[int, str] | None:
    run = artifact.get("workflow_run") or {}
    sha = run.get("head_sha", "")
    if not run.get("repository_id") or run["repository_id"] != run.get(
        "head_repository_id"
    ):
        return None
    return (int(run["id"]), sha) if run.get("id") and SHAS.fullmatch(sha) else None


def stage(run: Row, repository: str, sha: str) -> str | None:
    if (run.get("head_repository") or {}).get("full_name") != repository or run[
        "head_sha"
    ] != sha:
        return None
    workflow = run["path"].split("@", 1)[0]
    if workflow == PR_WORKFLOW:
        if run["event"] == "pull_request":
            return "PR"
        if run["event"] == "merge_group" and run["head_branch"].startswith(
            "gh-readonly-queue/main/"
        ):
            return "queue"
    if (
        workflow in OS_WORKFLOWS
        and run["head_branch"] == "main"
        and run["event"] == "schedule"
    ):
        return "nightly"
    return None


def members(
    repository: str, artifact: Row, names: set[str], download: Callable[[str], bytes]
) -> dict[str, bytes]:
    aid = int(artifact["id"])
    size = int(artifact["size_in_bytes"])
    if not 0 < size <= MAX_BYTES:
        raise ValueError(f"Oversized Actions artifact {aid}")
    raw = download(f"repos/{repository}/actions/artifacts/{aid}/zip")
    if len(raw) > MAX_BYTES or artifact.get("digest") != (
        f"sha256:{hashlib.sha256(raw).hexdigest()}"
    ):
        raise ValueError(f"Size or digest mismatch for Actions artifact {aid}")
    result = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        size = 0
        for entry in archive.infolist():
            if entry.filename not in names:
                continue
            size += entry.file_size
            if entry.filename in result or size > MAX_BYTES:
                raise ValueError(
                    f"Duplicate or oversized member in Actions artifact {aid}"
                )
            result[entry.filename] = archive.read(entry)
    return result


def coverage_map(data: bytes, sha: str, run: int, attempt: int) -> dict[str, set[str]]:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        raw = stream.read(MAX_OBSERVATION_BYTES + 1)
    if len(raw) > MAX_OBSERVATION_BYTES:
        raise ValueError("Oversized observation map")
    manifest = (observations := json.loads(raw))["manifest"]
    if (
        manifest["schema"] != 2
        or manifest["source_sha"] != sha
        or manifest["run_id"] != run
        or manifest["attempt"] != attempt
        or manifest["profile"] != "ubuntu-x64/all/skip-independent=false"
    ):
        raise ValueError("Observation map provenance mismatch")
    by_test: dict[str, set[str]] = {}
    for source, test_files in observations["modules"].items():
        if source.startswith("src/wgsextract_cli/"):
            for test_file in test_files:
                by_test.setdefault(test_file, set()).add(source)
    return by_test


def load_coverage(
    api: GitHub,
    start: datetime,
    end: datetime,
    download: Callable[[str], bytes],
) -> tuple[str | None, dict[str, set[str]]]:
    query = urlencode(
        {
            "event": "schedule",
            "branch": "main",
            "status": "completed",
            "created": f"{start:%Y-%m-%dT%H:%M:%SZ}..{end:%Y-%m-%dT%H:%M:%SZ}",
        }
    )
    runs = api.pages(f"actions/workflows/ubuntu_ci.yml/runs?{query}", "workflow_runs")
    candidates: list[tuple[Row, int, int, str]] = []
    for run in runs:
        run_id, attempt, sha = int(run["id"]), int(run["run_attempt"]), run["head_sha"]
        if stage(run, api.repository, sha) != "nightly" or not SHAS.fullmatch(sha):
            continue
        name = f"linux-test-observations-{attempt}"
        candidates.extend(
            (row, run_id, attempt, sha)
            for row in api.pages(
                f"actions/runs/{run_id}/artifacts?name={name}", "artifacts"
            )
            if row["name"] == name
            and artifact_origin(row) == (run_id, sha)
            and not row["expired"]
            and start <= stamp(row["created_at"]) < end
        )
    if not candidates:
        return None, {}
    artifact, run_id, attempt, sha = max(
        candidates, key=lambda item: (stamp(item[0]["created_at"]), int(item[0]["id"]))
    )
    contents = members(api.repository, artifact, {"linux.json.gz"}, download)
    return sha, coverage_map(contents["linux.json.gz"], sha, run_id, attempt)


@dataclass(frozen=True)
class Selected:
    artifact: Row
    run: int
    sha: str
    stage: str
    platform: str
    failed_run: bool


def sample(work: list[Selected]) -> list[Selected]:
    lanes: dict[tuple[str, str], list[Selected]] = defaultdict(list)
    selected = []
    for item in work:
        if item.stage == "nightly":
            selected.append(item)
        else:
            lanes[item.stage, item.platform].append(item)

    def spread(items: list[Selected], limit: int) -> list[Selected]:
        if len(items) <= limit:
            return items
        ordered = sorted(
            items, key=lambda item: (item.artifact["created_at"], item.artifact["id"])
        )
        return [
            ordered[(2 * i + 1) * len(ordered) // (2 * limit)] for i in range(limit)
        ]

    for items in lanes.values():
        failures = spread(
            [item for item in items if item.failed_run], PREMERGE_PER_LANE // 2
        )
        failed_shas = {item.sha for item in failures}
        used = {item.artifact["id"] for item in failures}
        related = spread(
            [
                item
                for item in items
                if item.artifact["id"] not in used
                and not item.failed_run
                and item.sha in failed_shas
            ],
            PREMERGE_PER_LANE // 4,
        )
        used.update(item.artifact["id"] for item in related)
        selected.extend(
            failures
            + related
            + spread(
                [item for item in items if item.artifact["id"] not in used],
                PREMERGE_PER_LANE - len(used),
            )
        )
    return selected


def collect(
    api: GitHub,
    start: datetime,
    end: datetime,
    as_of: datetime,
    tests: set[str],
    download: Callable[[str], bytes] = gh_api,
) -> Evidence:
    evidence = Evidence()
    evidence.map_sha, evidence.coverage = load_coverage(
        api, as_of - timedelta(days=7), as_of, download
    )
    artifacts = artifact_rows(api, start, end)
    if not artifacts:
        return evidence
    lower = start - timedelta(days=1)
    runs: dict[int, Row] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(
                api.runs_between,
                lower,
                end,
                workflow=path.rsplit("/", 1)[1],
            )
            for path in sorted({PR_WORKFLOW, *OS_WORKFLOWS})
        ]
        for future in futures:
            runs.update((run["id"], run) for run in future.result())
    work = []
    for artifact in artifacts:
        if not (origin := artifact_origin(artifact)):
            continue
        run_id, sha = origin
        if run_id not in runs:
            runs[run_id] = api.api("GET", f"actions/runs/{run_id}")
        run = runs[run_id]
        if not (lane := stage(run, api.repository, sha)):
            continue
        if artifact["expired"]:
            evidence.expired += 1
            continue
        work.append(
            Selected(
                artifact,
                run_id,
                sha,
                lane,
                ARTIFACT_OS[artifact["name"]],
                run.get("conclusion") == "failure",
            )
        )

    def read(item: Selected) -> tuple[list[Report], int]:
        artifact = item.artifact
        result = []
        unmapped = 0
        for suite, xml in members(api.repository, artifact, JUNIT, download).items():
            cases, missing = junit(xml, tests)
            unmapped += missing
            result.append(
                Report(
                    int(artifact["id"]),
                    item.run,
                    item.sha,
                    item.stage,
                    item.platform,
                    suite,
                    stamp(artifact["created_at"]),
                    cases,
                )
            )
        return result, unmapped

    chosen = sample(work)
    evidence.sampled_out = len(work) - len(chosen)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for reports, missing in pool.map(read, chosen):
            evidence.reports.extend(reports)
            evidence.without_junit += not reports
            evidence.unmapped += missing
    return evidence
