"""Replay ready PR pushes to estimate what the test result cache would skip.

For each pair of consecutive ready pushes of a merged PR where the earlier one
passed, the later push's #914 selection is compared with its dependency
hashes: a selected file whose hash equals the earlier push's would be skipped.
Hashes and selections are computed on each pushed head tree with the current
scripts (an approximation of the merge refs CI actually checks out).
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_select_tests import select_tests, selection_context  # noqa: E402
from scripts.ci_test_cache import Fingerprint, fingerprint  # noqa: E402
from scripts.ci_test_dependencies import ROOT  # noqa: E402

JOB = "replay"


def gh_json(*args: str) -> Any:
    return json.loads(subprocess.check_output(["gh", *args], text=True))


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def file_seconds(junit: list[Path]) -> dict[str, float]:
    """Per-test-file pytest seconds from JUnit reports of a full main run."""
    seconds: defaultdict[str, float] = defaultdict(float)
    for report in junit:
        for case in ET.parse(report).getroot().iter("testcase"):
            module = case.get("classname", "").split(".")
            for length in range(len(module), 0, -1):
                path = "/".join(module[:length]) + ".py"
                if (ROOT / path).is_file():
                    seconds[path] += float(case.get("time", 0))
                    break
    return dict(seconds)


class Trees:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.fingerprints: dict[str, Fingerprint] = {}

    def checkout(self, sha: str) -> Path:
        tree = self.directory / sha
        if not tree.exists():
            subprocess.run(["git", "fetch", "-q", "origin", sha], cwd=ROOT, check=True)
            git("worktree", "add", "-q", "--detach", str(tree), sha)
        return tree

    def fingerprint(self, sha: str) -> Fingerprint:
        if sha not in self.fingerprints:
            self.fingerprints[sha] = fingerprint(JOB, repository=self.checkout(sha))
        return self.fingerprints[sha]

    def remove(self) -> None:
        for tree in self.directory.iterdir():
            git("worktree", "remove", "--force", str(tree))


def ready_runs(repository: str, branch: str) -> list[tuple[str, str]]:
    pages = gh_json(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{repository}/actions/runs?branch={branch}&event=pull_request&per_page=100",
    )
    runs = sorted(
        (
            run
            for page in pages
            for run in page["workflow_runs"]
            if run["path"] == ".github/workflows/pr_ci.yml"
            and run["display_title"].endswith("/ ready")
            and "labeled" not in run["display_title"]
        ),
        key=lambda run: run["created_at"],
    )
    # Ready runs in order; a re-run of an unchanged head (ready -> draft ->
    # ready) is a pair too, whose hashes are all unchanged.
    return [(run["head_sha"], run["conclusion"] or "") for run in runs]


def replay(
    repository: str, limit: int, output: Path, seconds: dict[str, float]
) -> dict[str, Any]:
    prs = gh_json(
        "pr",
        "list",
        "--repo",
        repository,
        "--state",
        "merged",
        "--limit",
        str(limit),
        "--json",
        "number,mergedAt,headRefName,baseRefOid,mergeCommit",
    )
    trees = Trees(ROOT / "scratch" / "cache-replay-trees")
    trees.directory.mkdir(parents=True, exist_ok=True)
    records = []
    try:
        for pr in sorted(prs, key=lambda pr: pr["mergedAt"]):
            heads = ready_runs(repository, pr["headRefName"])
            for (previous, conclusion), (head, _) in itertools.pairwise(heads):
                if conclusion != "success":
                    continue
                try:
                    tree = trees.checkout(head)
                    trees.checkout(previous)
                    base = git("merge-base", f"{pr['mergeCommit']['oid']}^1", head)
                except subprocess.CalledProcessError:
                    continue
                paths = git(
                    "diff", "--no-renames", "--name-only", f"{base}...{head}"
                ).split()
                all_tests = set(selection_context(tree.resolve()).tests)
                selection = select_tests(paths, repository=tree)
                selected = all_tests if selection == "all" else set(selection)
                before = trees.fingerprint(previous)
                after = trees.fingerprint(head)
                # The cache's reuse rule: same hash, no sensitive file deleted.
                skipped = {
                    test
                    for test in selected
                    if test in before.hashes
                    and after.names >= before.names
                    and before.hashes.get(test) == after.hashes.get(test)
                }
                records.append(
                    {
                        "pr": pr["number"],
                        "previous": previous,
                        "head": head,
                        "selection_all": selection == "all",
                        "selected": len(selected),
                        "skipped": len(skipped),
                        "selected_seconds": sum(seconds.get(t, 0) for t in selected),
                        "skipped_seconds": sum(seconds.get(t, 0) for t in skipped),
                        "skipped_tests": sorted(skipped),
                    }
                )
                print(
                    f"PR {pr['number']} {head[:8]}: skip {len(skipped)}/{len(selected)}",
                    flush=True,
                )
    finally:
        trees.remove()
    summary = {
        "prs": len(prs),
        "ready_repushes_after_green": len(records),
        "selected_files": sum(r["selected"] for r in records),
        "skipped_files": sum(r["skipped"] for r in records),
        "selected_pytest_seconds_per_os": sum(r["selected_seconds"] for r in records),
        "skipped_pytest_seconds_per_os": sum(r["skipped_seconds"] for r in records),
        "method": __doc__,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "cache_replay.json").write_text(
        json.dumps({"summary": summary, "records": records}, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="WGSExtract/wgsextract-cli")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--output", type=Path, default=Path("out/915"))
    parser.add_argument(
        "--junit",
        type=Path,
        nargs="*",
        default=[],
        help="JUnit reports of a full main run, for per-file pytest seconds.",
    )
    args = parser.parse_args()
    summary = replay(args.repo, args.limit, args.output, file_seconds(args.junit))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
