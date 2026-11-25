"""Dispatch and verify the first-party release preflight for one tagged commit."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

BUILD_GATES = (
    "linux_aligners.yml",
    "macos_aligners.yml",
    "linux_deepvariant.yml",
    "macos_deepvariant.yml",
    "macos_clair3.yml",
    "gui-tauri-installer.yml",
    "pharmcat_e2e.yml",
    "gkl_windows.yml",
    "gatk_windows.yml",
    "cli_smoke.yml",
)
GUI = "gui-distribution.yml"
LAB = "install_lab.yml"


def api(endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    args = ["gh", "api", "-H", "X-GitHub-Api-Version: 2026-03-10", endpoint]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    response = subprocess.run(
        args,
        input=None if payload is None else json.dumps(payload),
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    result: dict[str, Any] = json.loads(response.stdout)
    return result


def dispatch(repository: str, ref: str, workflow: str, **inputs: str | bool) -> int:
    run = api(
        f"repos/{repository}/actions/workflows/{workflow}/dispatches",
        {"ref": ref, "inputs": inputs},
    )
    run_id = run["workflow_run_id"]
    if type(run_id) is not int or run_id < 1:
        raise ValueError(f"{workflow} did not return a run ID")
    print(
        f"{workflow}: https://github.com/{repository}/actions/runs/{run_id}", flush=True
    )
    return run_id


def successful_attempt(
    repository: str, sha: str, workflow: str, run_id: int
) -> int | None:
    run = api(f"repos/{repository}/actions/runs/{run_id}")
    if (
        run.get("id") != run_id
        or (run.get("head_repository") or {}).get("full_name") != repository
        or run.get("path") != f".github/workflows/{workflow}"
        or run.get("event") != "workflow_dispatch"
        or run.get("head_sha") != sha
    ):
        raise ValueError(f"{workflow} run {run_id} is not a dispatch of {sha}")
    if run["status"] != "completed":
        return None
    if run.get("conclusion") != "success":
        raise ValueError(f"{workflow} run {run_id} concluded {run.get('conclusion')}")
    attempt = run["run_attempt"]
    if type(attempt) is not int or attempt < 1:
        raise ValueError(f"{workflow} run {run_id} has no valid attempt")
    return attempt


def release_artifact(repository: str, gui_id: int) -> int:
    result = api(
        f"repos/{repository}/actions/runs/{gui_id}/artifacts?name=gui-release-assets"
    )
    artifacts = result["artifacts"]
    if (
        len(artifacts) != 1
        or artifacts[0]["name"] != "gui-release-assets"
        or artifacts[0]["expired"]
        or type(artifacts[0]["id"]) is not int
        or artifacts[0]["id"] < 1
    ):
        raise ValueError(f"GUI distribution {gui_id} has no unique release artifact")
    artifact_id: int = artifacts[0]["id"]
    return artifact_id


def preflight(repository: str, tag: str, sha: str) -> tuple[int, int]:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag) or not re.fullmatch(
        "[0-9a-f]{40}", sha
    ):
        raise ValueError("Expected a release tag and its full commit SHA")
    gui_id = dispatch(repository, tag, GUI, tag=tag, candidate=True)
    pending = {
        workflow: dispatch(repository, tag, workflow) for workflow in BUILD_GATES
    }
    pending[GUI] = gui_id
    gui_attempt: int | None = None
    artifact: int | None = None
    while pending:
        for workflow, run_id in list(pending.items()):
            attempt = successful_attempt(repository, sha, workflow, run_id)
            if attempt is not None:
                del pending[workflow]
                if workflow == GUI:
                    gui_attempt = attempt
                    artifact = release_artifact(repository, gui_id)
                    pending[LAB] = dispatch(
                        repository, tag, LAB, app_run=str(gui_id), candidate=True
                    )
        if pending:
            time.sleep(30)
    if (
        artifact is None
        or successful_attempt(repository, sha, GUI, gui_id) != gui_attempt
        or release_artifact(repository, gui_id) != artifact
    ):
        raise ValueError("GUI distribution changed during preflight")
    return gui_id, artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        run_id, artifact_id = preflight(args.repo, args.tag, args.sha)
        with args.output.open("a", encoding="utf-8") as output:
            output.write(f"gui_run={run_id}\ngui_artifact={artifact_id}\n")
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"Release preflight API failed: {error.stderr}")
    except (KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Release preflight failed: {error}\n")


if __name__ == "__main__":
    main()
