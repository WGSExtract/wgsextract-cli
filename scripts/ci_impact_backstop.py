"""Attribute daily Linux failures to verified symbol-selection omissions."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.ci_github import GitHub
from scripts.ci_impact_results import file_results
from scripts.ci_issues import IssueReports

SHA = re.compile(r"[0-9a-f]{40}")
TEST = re.compile(r"tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]+\.py")


def failing_targets(junit: Path, files: set[str]) -> dict[str, dict[str, str]]:
    """Keep the exact parameterized cases where xUnit exposes their node IDs."""
    targets: dict[str, dict[str, str]] = {}
    modules = {file.removesuffix(".py").replace("/", "."): file for file in files}
    for case in ET.parse(junit).iter("testcase"):
        error = case.find("error")
        failure = error if error is not None else case.find("failure")
        if failure is None:
            continue
        detail = (failure.text or "").strip().splitlines()
        last_error = next(
            (
                line.strip()
                for line in reversed(detail)
                if line.lstrip().startswith("E ")
            ),
            "",
        )
        signature = " ".join(
            (failure.tag + " " + failure.get("message", "") + " " + last_error).split()
        )
        if error is not None and error.get("message") == "collection failure":
            if file := modules.get(case.get("name", "")):
                targets.setdefault(file, {})[file] = signature
            continue
        name = case.get("name", "")
        classname = case.get("classname", "")
        if ".tests." in classname:
            classname = "tests." + classname.split(".tests.", 1)[1]
        if not name or "\n" in name or "\r" in name:
            continue
        prefix = classname
        while prefix:
            if file := modules.get(prefix):
                suffix = classname[len(prefix) :].lstrip(".").replace(".", "::")
                target = "::".join(part for part in (file, suffix, name) if part)
                targets.setdefault(file, {})[target] = signature
                break
            prefix = prefix.rpartition(".")[0]
    return targets


def first_bad(
    commits: list[str], rerun: Callable[[str], str]
) -> tuple[str | None, str]:
    """Scan in order: a fix followed by a regression defeats binary search."""
    for commit in commits:
        verdict = rerun(commit)
        if verdict == "pass":
            continue
        if verdict != "fail":
            return None, f"unverifiable result at {commit}"
        if rerun(commit) != "fail":
            return None, f"non-reproducible result at {commit}"
        return commit, "reproduced"
    return None, "not reproduced"


def verified_omission(
    receipt: dict[str, Any], tree: str, head: str, test: str, run: int
) -> bool:
    return (
        receipt.get("schema") == 1
        and receipt.get("run_id") == run
        and receipt.get("pr_head") == head
        and receipt.get("tested_git_tree") == tree
        and receipt.get("run_outcome") == "success"
        and receipt.get("gate_mode") == "symbol"
        and test in receipt.get("actual_selected", ())
        and test not in receipt.get("effective_selected", ())
        and test in receipt.get("variants", {}).get("symbol", {}).get("omitted", ())
    )


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def green_nightly(gh: GitHub, root: Path, run: int, head: str) -> str | None:
    runs = gh.api(
        "GET",
        "actions/workflows/ubuntu_ci.yml/runs?event=schedule&branch=main&per_page=15",
    )
    ancestry = set(git(root, "rev-list", "--first-parent", head).splitlines())
    for candidate in runs["workflow_runs"]:
        sha = candidate["head_sha"]
        if candidate["id"] >= run or sha not in ancestry or sha == head:
            continue
        jobs = gh.pages(
            f"actions/runs/{int(candidate['id'])}/jobs?filter=latest", "jobs"
        )
        for job in jobs:
            if job["name"] == "test-ubuntu / all" and any(
                step["name"] == "Run Tests" and step["conclusion"] == "success"
                for step in job.get("steps", ())
            ):
                return str(sha)
    return None


def replay(
    root: Path, workspace: Path, sha: str, test: str, node: str, signature: str
) -> str:
    """Use the already prepared local tools; never install historical inputs."""
    if not SHA.fullmatch(sha) or not TEST.fullmatch(test):
        return "unknown"
    worktree = workspace / "tree"
    junit = workspace / "replay.xml"
    junit.unlink(missing_ok=True)
    git(root, "worktree", "add", "--detach", str(worktree), sha)
    try:
        if not (worktree / test).is_file() or any(
            (worktree / name).read_bytes() != (root / name).read_bytes()
            for name in ("pixi.lock", "pixi.toml", "pyproject.toml")
        ):
            return "unknown"
        for name in ("reference", ".pixi"):
            prepared = root / name
            historical = worktree / name
            if prepared.is_dir() and not historical.exists():
                historical.symlink_to(prepared, target_is_directory=True)
        env = os.environ.copy()
        env.pop("GH_TOKEN", None)
        env["PYTHONPATH"] = os.pathsep.join(
            map(str, (worktree, worktree / "src", worktree / "tests"))
        )
        with (workspace / "replay.log").open("a", encoding="utf-8") as log:
            log.write(f"\n{sha} {test}\n")
            log.flush()
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        "-n",
                        "0",
                        "--basetemp=out/replay",
                        f"--junitxml={junit}",
                        node,
                    ],
                    cwd=worktree,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=360,
                )
            except subprocess.TimeoutExpired:
                return "unknown"
        if proc.returncode not in {0, 1, 2} or not junit.is_file():
            return "unknown"
        results, unmapped = file_results(junit, {test})
        row = results.get(test)
        if row is None or unmapped:
            return "unknown"
        if proc.returncode in {1, 2} and row["failed"]:
            found = failing_targets(junit, {test}).get(test, {}).get(node)
            return "fail" if found == signature else "unknown"
        return (
            "pass"
            if proc.returncode == 0 and row["passed"] and not row["skipped"]
            else "unknown"
        )
    except (ET.ParseError, OSError, ValueError):
        return "unknown"
    finally:
        git(root, "worktree", "remove", "--force", str(worktree))


def receipt_from_run(gh: GitHub, run: int) -> dict[str, Any]:
    artifacts = gh.api(
        "GET", f"actions/runs/{run}/artifacts?name=test-selection-shadow"
    )
    found = [a for a in artifacts["artifacts"] if not a["expired"]]
    if len(found) != 1 or found[0]["size_in_bytes"] > 2_000_000:
        return {}
    artifact = int(found[0]["id"])
    binary = subprocess.check_output(
        ["gh", "api", f"repos/{gh.repository}/actions/artifacts/{artifact}/zip"]
    )
    with zipfile.ZipFile(io.BytesIO(binary)) as archive:
        entry = archive.getinfo("test-selection-shadow.json")
        if entry.file_size > 4_000_000:
            return {}
        data = json.loads(archive.read(entry))
    return data if isinstance(data, dict) else {}


def attribution(
    gh: GitHub, root: Path, commit: str, test: str
) -> dict[str, Any] | None:
    pulls = gh.api("GET", f"commits/{commit}/pulls")
    matching = [
        pr
        for pr in pulls
        if pr.get("merged_at")
        and pr.get("merge_commit_sha") == commit
        and pr["base"]["ref"] == "main"
        and pr["head"]["repo"]
        and pr["head"]["repo"]["full_name"] == gh.repository
    ]
    if len(matching) != 1:
        return None
    pr = matching[0]
    head = pr["head"]["sha"]
    if not SHA.fullmatch(head):
        return None
    tree = git(root, "rev-parse", f"{commit}^{{tree}}")
    runs = gh.api(
        "GET",
        f"actions/workflows/pr_ci.yml/runs?head_sha={head}&event=pull_request&status=completed&per_page=15",
    )
    for run in runs["workflow_runs"]:
        if run["head_sha"] != head or run["conclusion"] != "success":
            continue
        run_id = int(run["id"])
        try:
            receipt = receipt_from_run(gh, run_id)
        except (
            subprocess.CalledProcessError,
            KeyError,
            ValueError,
            zipfile.BadZipFile,
        ):
            continue
        if verified_omission(receipt, tree, head, test, run_id):
            return {
                "pr": int(pr["number"]),
                "commit": commit,
                "test": test,
                "rule": "symbol-no-matching-edge",
                "pr_run": run_id,
                "rule_reasons": receipt["rule_reasons"],
            }
    return None


def investigate(gh: GitHub, junit: Path, run: int, output: Path) -> None:
    root = Path.cwd().resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("[]\n", encoding="utf-8")
    if not junit.is_file():
        print("No regular Linux JUnit; selector attribution unavailable.")
        return
    tests = {p.as_posix() for p in Path("tests").rglob("test_*.py")}
    failed = failing_targets(junit, {test for test in tests if TEST.fullmatch(test)})
    head = git(root, "rev-parse", "HEAD")
    baseline = green_nightly(gh, root, run, head)
    if not baseline or not failed:
        print("No previous full nightly pass or no identifiable failing test files.")
        return
    commits = git(
        root, "rev-list", "--reverse", "--first-parent", f"{baseline}..{head}"
    ).splitlines()
    if len(commits) > 100 or sum(map(len, failed.values())) > 20:
        print("Nightly replay exceeds the automatic attribution budget.")
        return
    reports: list[dict[str, Any]] = []
    for test, nodes in sorted(failed.items()):
        for node, signature in sorted(nodes.items()):

            def runner(
                sha: str, test: str = test, node: str = node, signature: str = signature
            ) -> str:
                return replay(root, output.parent, sha, test, node, signature)

            if runner(head) != "fail" or runner(baseline) != "pass":
                print(f"{test}: no reproducible current failure or passing baseline")
                continue
            bad, why = first_bad(commits, runner)
            print(f"{test}: {bad or 'unattributed'} ({why})")
            if bad and (miss := attribution(gh, root, bad, test)):
                if not any(r["test"] == test and r["commit"] == bad for r in reports):
                    reports.append(miss)
                    output.write_text(json.dumps(reports) + "\n", encoding="utf-8")


def publish(gh: GitHub, run: int, report: Path) -> None:
    issues = IssueReports(gh)
    for miss in json.loads(report.read_text(encoding="utf-8")):
        test, commit, pr, pr_run = (
            miss["test"],
            miss["commit"],
            miss["pr"],
            miss["pr_run"],
        )
        if not (
            TEST.fullmatch(test)
            and SHA.fullmatch(commit)
            and all(isinstance(n, int) and n > 0 for n in (pr, pr_run))
        ):
            raise ValueError("Invalid selector miss receipt")
        marker = hashlib.sha256(f"{commit}:{test}".encode()).hexdigest()
        issues.issue(
            f"Linux symbol selector missed {test} in PR #{pr}",
            f"<!-- ci-selector-miss:{marker} -->",
            f"PR [#{pr} (first bad merge)](https://github.com/{gh.repository}/pull/{pr}) "
            f"introduced reproducible failure in `{test}` at `{commit}`.\n\n"
            "Rule: `symbol-no-matching-edge` (the legacy selector included the file, "
            "but the enforced symbol set omitted it on the identical Git tree).\n\n"
            f"[PR selection receipt](https://github.com/{gh.repository}/actions/runs/{pr_run}); "
            f"[daily replay](https://github.com/{gh.repository}/actions/runs/{run}). "
            "Add a selector regression and the missing dependency or contract edge.",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    gh = GitHub(args.repository)
    if args.junit is None:
        publish(gh, args.run, args.report)
    else:
        investigate(gh, args.junit, args.run, args.report)


if __name__ == "__main__":
    main()
