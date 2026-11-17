"""Reuse only a successful push run of this workflow at the exact nightly SHA."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from urllib.parse import urlencode


def already_passed(repository: str, workflow: str, sha: str) -> bool:
    query = urlencode(
        {"branch": "main", "event": "push", "status": "success", "head_sha": sha}
    )
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/actions/workflows/{workflow}/runs?{query}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        runs = json.loads(result.stdout)["workflow_runs"]
        return any(
            isinstance(run, dict)
            and run.get("head_sha") == sha
            and run.get("head_branch") == "main"
            and run.get("event") == "push"
            and run.get("conclusion") == "success"
            and isinstance(run.get("head_repository"), dict)
            and run["head_repository"].get("full_name") == repository
            for run in runs
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    skip = already_passed(args.repository, args.workflow, args.sha)
    with args.output.open("a", encoding="utf-8") as output:
        output.write(f"skip={str(skip).lower()}\n")
    print(
        "Successful push already covers this SHA."
        if skip
        else "Running nightly coverage."
    )


if __name__ == "__main__":
    main()
