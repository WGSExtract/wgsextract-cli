"""Failure evidence and retry decisions, independent of GitHub transport."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from scripts.ci_github import Evidence

TEST_ID = re.compile(r"(?:FAILED|ERROR)\s+([^\s]+\.py::.+?)(?:\s+-\s|$)", re.M)
BAD = {"failure", "cancelled", "timed_out", "startup_failure"}
ROUTED_MAIN = {
    "ubuntu": "ubuntu_ci.yml",
    "macos": "macos_ci.yml",
    "windows": "windows_ci.yml",
    "arm64": "linux_arm64.yml",
    "smoke": "cli_smoke.yml",
    "install": "install_lab.yml",
    "Documentation freshness": "ubuntu_ci.yml",
}


def safe(value: str) -> str:
    return html.escape(value).replace("@", "@\u200b").replace("`", "'")


@dataclass
class Failure:
    job_id: int
    job: str
    step: str
    classification: str
    tests: list[str]
    errors: list[str]
    conclusion: str
    executions: int = 1
    source_attempt: int = 1
    started: bool = True
    missing_artifacts: list[str] = field(default_factory=list)
    upstream_job_ids: list[int] = field(default_factory=list)
    root_executions: int | None = None


def classify(job: dict[str, Any], evidence: Evidence) -> Failure:
    steps = job.get("steps", [])
    failed_steps = [step["name"] for step in steps if step.get("conclusion") in BAD]
    combined = evidence.failures + "\n" + evidence.log
    tests = sorted(set(TEST_ID.findall(combined)))
    kind = "test failure"
    if (
        job.get("conclusion") == "cancelled"
        and not steps
        or re.search(
            r"lost communication with the server|runner has received a shutdown signal",
            evidence.failures,
            re.I,
        )
    ):
        kind = "lost runner"
    elif not tests and re.search(
        r"Upstream package mirror unreachable|Could not resolve host|"
        r"Temporary failure in name resolution|Connection reset by peer|"
        r"HTTP (?:Error )?(?:502|503|504)\b",
        evidence.failures,
        re.I,
    ):
        kind = "mirror/network"
    lines = [
        re.sub(r"^\d{4}-\d\d-\d\dT\S+\s*", "", line).strip()
        for line in combined.splitlines()
        if re.search(
            r"error|failed|lost communication|shutdown signal|unreachable", line, re.I
        )
    ]
    missing_artifacts: list[str] = []
    if (
        not tests
        and len(failed_steps) == 1
        and re.search(
            r"^(?:get|download|fetch|retrieve)\b|actions/download-artifact@",
            failed_steps[0],
            re.I,
        )
    ):
        missing_artifacts = sorted(
            {
                name.strip().strip("'\"")
                for name in re.findall(
                    r"\bArtifact not found for name:[ \t]*([^\r\n]+)",
                    evidence.failures,
                    re.I,
                )
            }
        )
    return Failure(
        int(job["id"]),
        job["name"],
        failed_steps[0] if failed_steps else "(before first step)",
        kind,
        tests,
        [line[:300] for line in lines[:5]],
        str(job["conclusion"]),
        started=bool(steps),
        missing_artifacts=missing_artifacts,
    )


def classify_lost_dependencies(failures: list[Failure]) -> None:
    producers: dict[str, list[Failure]] = {}
    for failure in failures:
        if failure.classification != "lost runner" or failure.started:
            continue
        name, _, matrix = failure.job.partition(" (")
        if not re.search(r"\b(?:build|tauri)\b", name, re.I):
            continue
        # Build matrix targets identify their artifact in the API job name.
        targets = (
            matrix.removesuffix(")").split(",")
            if matrix
            else [name.rsplit(" / ", 1)[-1]]
        )
        for target in targets:
            producers.setdefault(target.strip(), []).append(failure)
    for failure in failures:
        if (
            failure.classification != "test failure"
            or failure.conclusion != "failure"
            or not failure.missing_artifacts
            or not all(artifact in producers for artifact in failure.missing_artifacts)
        ):
            continue
        upstream = {
            root.job_id: root
            for artifact in failure.missing_artifacts
            for root in producers[artifact]
        }
        failure.classification = "lost runner"
        failure.upstream_job_ids = sorted(upstream)
        failure.root_executions = max(root.executions for root in upstream.values())


def signature(failure: Failure) -> tuple[str, str, str]:
    job = failure.job
    prefix, separator, rest = job.partition(" / ")
    if separator and prefix in ROUTED_MAIN:
        job = rest
    error = next(
        (
            line
            for line in failure.errors
            if not re.search(
                r"Process completed with exit code|operation was canceled", line, re.I
            )
        ),
        "(no detailed error)",
    )
    return job, failure.step, error


def matches(failure: Failure, main: list[Failure]) -> bool:
    if failure.classification != "test failure":
        return False
    for baseline in main:
        if failure.tests and set(failure.tests) & set(baseline.tests):
            return True
        if (
            not failure.tests
            and not baseline.tests
            and signature(failure) == signature(baseline)
        ):
            return True
    return False


def retryable(failure: Failure, *, rerun_tests: bool) -> bool:
    limit = {"lost runner": 1, "mirror/network": 2, "test failure": 2}.get(
        failure.classification, 0
    )
    return (
        (
            failure.root_executions
            if failure.root_executions is not None
            else failure.executions
        )
        <= limit
        and (
            failure.conclusion != "cancelled" or failure.classification == "lost runner"
        )
        and (failure.classification != "test failure" or rerun_tests)
    )
