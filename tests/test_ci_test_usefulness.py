"""Fixture-backed weekly review; no GitHub credentials or network needed."""

import gzip
import hashlib
import io
import json
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.ci_efficiency_report import stamp
from scripts.ci_github import GitHub
from scripts.ci_test_usefulness import summarize
from scripts.ci_test_usefulness_inputs import Case, Report, junit
from scripts.ci_test_usefulness_report import measure, publish

REPOSITORY = "owner/repo"
TEST_FILE = "tests/test_fast.py"
SOURCE = "src/wgsextract_cli/feature.py"
SHA = "a" * 40


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.org",
            *args,
        ],
        text=True,
    ).strip()


class FixtureGitHub(GitHub):
    def __init__(self) -> None:
        super().__init__(REPOSITORY)
        self.runs: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self.downloads: dict[int, bytes] = {}
        self.issues: list[dict[str, Any]] = []

    def runs_between(
        self, start: datetime, end: datetime, *, workflow: str = "", event: str = ""
    ) -> list[dict[str, Any]]:
        return [
            r
            for r in self.runs
            if start <= stamp(r["created_at"]) <= end
            and r["path"].endswith("/" + workflow)
            and (not event or r["event"] == event)
        ]

    def api(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        url = urlsplit(path)
        query = parse_qs(url.query)
        name = query.get("name", [""])[0]
        if method == "GET":
            if url.path == "actions/artifacts":
                return {"artifacts": [a for a in self.artifacts if a["name"] == name]}
            if url.path == "actions/workflows/ubuntu_ci.yml/runs":
                return {
                    "workflow_runs": [r for r in self.runs if r["event"] == "schedule"]
                }
            if url.path.startswith("actions/runs/") and url.path.endswith("/artifacts"):
                return {
                    "artifacts": [
                        a
                        for a in self.artifacts
                        if a["name"] == name
                        and a["workflow_run"]["id"] == int(url.path.split("/")[2])
                    ]
                }
            if url.path == "issues":
                return [issue.copy() for issue in self.issues]
        assert data is not None, (method, path)
        if method == "POST" and url.path == "issues":
            issue = {
                **data,
                "number": len(self.issues) + 1,
                "state": "open",
                "user": {"login": "github-actions[bot]"},
                "labels": [{"name": label} for label in data["labels"]],
            }
            self.issues.append(issue)
            return issue
        issue = self.issues[int(url.path.split("/")[1]) - 1]
        if method == "PATCH":
            issue.update(data)
        elif method == "POST" and url.path.endswith("/labels"):
            issue["labels"] += [{"name": label} for label in data["labels"]]
        else:
            raise AssertionError((method, path))
        return issue

    def download(self, path: str) -> bytes:
        return self.downloads[int(path.split("/")[-2])]

    def run(
        self,
        number: int,
        created: str,
        xml: str,
        *,
        nightly: bool = False,
        zip_digest: bool = True,
    ) -> None:
        self.runs.append(
            {
                "id": number,
                "head_sha": SHA,
                "created_at": created,
                "run_attempt": 1,
                "head_repository": {"full_name": REPOSITORY},
                "conclusion": "success",
                "path": ".github/workflows/"
                + ("ubuntu_ci.yml" if nightly else "pr_ci.yml"),
                "event": "schedule" if nightly else "pull_request",
                "head_branch": "main" if nightly else "work",
            }
        )
        self.artifact(number, created, "tests-ubuntu-all", "pytest.xml", xml.encode())
        if not zip_digest:
            self.artifacts[-1]["digest"] = "sha256:wrong"

    def artifact(
        self,
        run: int,
        created: str,
        name: str,
        member: str,
        data: bytes,
        *,
        expired: bool = False,
    ) -> None:
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            zipped.writestr(member, data)
        aid = len(self.artifacts) + 1
        self.downloads[aid] = raw.getvalue()
        self.artifacts.append(
            {
                "id": aid,
                "name": name,
                "created_at": created,
                "expired": expired,
                "size_in_bytes": len(self.downloads[aid]),
                "digest": "sha256:" + hashlib.sha256(self.downloads[aid]).hexdigest(),
                "workflow_run": {
                    "id": run,
                    "head_sha": SHA,
                    "repository_id": 3,
                    "head_repository_id": 3,
                },
            }
        )


def pytest_xml(names: dict[str, str]) -> str:
    tests = "".join(
        f'<testcase classname="tests.test_fast" name="{name}" time="10">{outcome}</testcase>'
        for name, outcome in names.items()
    )
    return f"<testsuites><testsuite>{tests}</testsuite></testsuites>"


def test_weekly_review_links_cost_and_candidates_to_observed_code_changes(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / TEST_FILE).parent.mkdir()
    (tmp_path / SOURCE).parent.mkdir(parents=True)
    (tmp_path / SOURCE).write_text("value = 1\n", encoding="utf-8")
    (tmp_path / TEST_FILE).write_text(
        "def test_slow_duplicate():\n    assert check(1)\n\n"
        "def test_same():\n    assert check(1)\n\n"
        "def test_variant():\n    assert check(2)\n\n"
        "def test_flake():\n    assert another_check()\n",
        encoding="utf-8",
    )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Initial tests and source")
    (tmp_path / SOURCE).write_text("value = 2\n", encoding="utf-8")
    git(tmp_path, "commit", "-qam", "Change covered source")
    changed = git(tmp_path, "rev-parse", "HEAD")

    api = FixtureGitHub()
    outcomes = {
        "test_slow_duplicate": "",
        "test_same": "",
        "test_variant": "",
        "test_flake": "",
    }
    for hour in range(1, 16):
        api.run(
            hour,
            f"2026-11-18T{hour:02d}:00:00Z",
            pytest_xml(
                {
                    **outcomes,
                    "test_flake": "<failure/>" if hour == 1 else "",
                }
            ),
        )
    api.run(16, "2026-11-22T10:00:00Z", pytest_xml(outcomes), nightly=True)
    api.artifact(
        16,
        "2026-11-22T10:10:00Z",
        "linux-test-observations-1",
        "linux.json.gz",
        gzip.compress(
            json.dumps(
                {
                    "manifest": {
                        "schema": 2,
                        "source_sha": SHA,
                        "run_id": 16,
                        "attempt": 1,
                        "profile": "ubuntu-x64/all/skip-independent=false",
                    },
                    "modules": {SOURCE: [TEST_FILE]},
                }
            ).encode()
        ),
    )
    api.run(17, "2026-11-23T00:00:00Z", pytest_xml({"test_flake": "<error/>"}))
    api.artifact(
        1,
        "2026-11-18T01:06:00Z",
        "tests-ubuntu-shard-2",
        "pytest.xml",
        b"unavailable",
        expired=True,
    )
    output = tmp_path / "report.json"
    body = measure(
        api, tmp_path, stamp("2026-11-23T06:43:00Z"), 90, output, download=api.download
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["expired_artifacts"] == 1
    assert report["coverage_by_test_file"][TEST_FILE][SOURCE] == {
        "sha": changed,
        "date": git(tmp_path, "show", "-s", "--format=%cs"),
        "path": SOURCE,
    }
    tests = {row["test"].split("::")[1]: row for row in report["tests"]}
    assert tests["test_slow_duplicate"]["nightly_overlap_seconds"] == 150
    assert tests["test_slow_duplicate"]["median_seconds"] == 10
    assert tests["test_flake"]["failures"] == 1
    assert tests["test_flake"]["same_pr_head_failure_then_pass_runs"] == [[1, 2]]
    assert tests["test_flake"]["same_sha_failure_then_pass_runs"] == []
    move = body.split("### Move to nightly")[1].split("###")[0]
    assert "test_slow_duplicate" in move and "test_flake" not in move
    assert f"/commit/{changed}" in move
    assert "Parametrize" in body and "review removing" in body
    assert "16 JUnit artifacts" in body and "/actions/runs/90" in body


def test_junit_skips_and_teardown_errors_are_not_successes() -> None:
    data = b"""<testsuite>
    <testcase classname="tests.test_fast.Scenarios" name="test_case[1]" time="2"/>
    <testcase classname="tests.test_fast.Scenarios" name="test_case[1]" time="1"><error/></testcase>
    <testcase classname="test_fast" name="test_missing_tool" time="NaN"><skipped/></testcase>
    <testcase classname="tests.test_removed" name="test_removed"/>
    </testsuite>"""
    cases, unmapped = junit(data, {TEST_FILE})
    assert cases == (
        Case(f"{TEST_FILE}::Scenarios::test_case[1]", "fail", 3),
        Case(f"{TEST_FILE}::test_missing_tool", "skip", 0),
    )
    assert unmapped == 1
    with pytest.raises(ValueError, match="document type"):
        junit(b"<!DOCTYPE testsuite><testsuite/>", {TEST_FILE})


def test_passing_different_code_or_os_does_not_make_a_flake() -> None:
    node = f"{TEST_FILE}::test_flake"
    reports = [
        Report(
            i,
            i,
            sha,
            stage,
            os,
            suite,
            stamp(f"2026-11-18T0{i}:00:00Z"),
            (Case(node, result, 10),),
        )
        for i, (sha, stage, os, suite, result) in enumerate(
            [
                (SHA, "PR", "ubuntu", "pytest.xml", "fail"),
                ("b" * 40, "PR", "ubuntu", "pytest.xml", "pass"),
                (SHA, "PR", "windows", "pytest.xml", "pass"),
                (SHA, "nightly", "ubuntu", "pytest.xml", "pass"),
                (SHA, "PR", "ubuntu", "e2e-fake.xml", "pass"),
                (SHA, "PR", "ubuntu", "pytest.xml", "skip"),
            ],
            start=1,
        )
    ]
    result = summarize(reports)[node]
    assert not result.same_pr_head and not result.flakes


def test_busy_lane_retains_same_sha_failure_and_pass_in_the_sample(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / TEST_FILE).parent.mkdir()
    (tmp_path / TEST_FILE).write_text("def test_case():\n    pass\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    api = FixtureGitHub()
    for minute in range(50):
        api.run(
            minute + 1,
            f"2026-11-18T10:{minute:02d}:00Z",
            pytest_xml({"test_case": "<failure/>" if minute == 0 else ""}),
        )
        if minute > 1:
            unique_sha = f"{minute:040x}"
            api.runs[-1]["head_sha"] = unique_sha
            api.artifacts[-1]["workflow_run"]["head_sha"] = unique_sha
    api.runs[0]["conclusion"] = "failure"
    output = tmp_path / "report.json"
    body = measure(
        api, tmp_path, stamp("2026-11-23T06:43:00Z"), 90, output, download=api.download
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert 0 < report["sampled_out_artifacts"] < 50
    assert report["tests"][0]["same_pr_head_failure_then_pass_runs"] == [[1, 2]]
    assert "left out of the sample" in body


def test_duplicate_candidates_need_distinct_runs_not_several_platforms(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / TEST_FILE).parent.mkdir()
    (tmp_path / TEST_FILE).write_text(
        "def test_first():\n    assert check()\n\n"
        "def test_second():\n    assert check()\n",
        encoding="utf-8",
    )
    git(tmp_path, "add", ".")
    api = FixtureGitHub()
    xml = pytest_xml({"test_first": "", "test_second": ""})
    for run in (1, 2):
        created = f"2026-11-18T0{run}:00:00Z"
        api.run(run, created, xml)
        for os in ("windows", "macos"):
            api.artifact(run, created, f"tests-{os}-all", "pytest.xml", xml.encode())
    output = tmp_path / "report.json"
    measure(
        api, tmp_path, stamp("2026-11-23T06:43:00Z"), 90, output, download=api.download
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert all(row["passes"] == 6 for row in report["tests"])
    assert report["similar_tests"] == []


def test_corrupt_artifact_does_not_publish_a_false_clean_week(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    api = FixtureGitHub()
    api.run(1, "2026-11-18T10:00:00Z", pytest_xml({}), zip_digest=False)
    output = tmp_path / "report.json"
    with pytest.raises(ValueError, match="digest mismatch"):
        measure(
            api,
            tmp_path,
            stamp("2026-11-23T06:43:00Z"),
            90,
            output,
            download=api.download,
        )
    assert not output.exists()


def test_publication_updates_one_bot_issue_even_if_closed_or_relabelled() -> None:
    api = FixtureGitHub()
    marker = "<!-- weekly-test-usefulness:v1 -->\n"
    old = publish(api, marker + "Week one")
    issue = api.issues[0]
    issue.update(state="closed", labels=[{"name": "other"}])
    assert publish(api, marker + "Week two") == old
    assert publish(api, marker + "Week two") == old
    assert len(api.issues) == 1
    assert issue["body"].endswith("Week two")
    assert issue["state"] == "open"
    assert {label["name"] for label in issue["labels"]} == {"other", "efficiency"}
