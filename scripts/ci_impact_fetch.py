"""Restore a compatible Linux observation map from a trusted main workflow."""

from __future__ import annotations

import argparse
import hashlib
import io
import subprocess
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from scripts.ci_impact_shadow import fresh_observations, observation_map
from scripts.ci_merge_group_passes import Api, get_json, gh_api

WORKFLOW = "ubuntu_ci.yml"
MEMBER = "linux.json.gz"
MAX_BYTES = 32 * 1024 * 1024


def _trusted_runs(api: Api, repository: str) -> list[dict[str, Any]]:
    prefix = f"repos/{repository}/actions/workflows/{WORKFLOW}/runs"
    recent = (
        (datetime.now(timezone.utc) - timedelta(hours=48))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    runs: list[dict[str, Any]] = []
    for event in ("schedule", "workflow_dispatch"):
        query = urlencode(
            {
                "event": event,
                "status": "success",
                "branch": "main",
                "per_page": 50,
            }
        )
        runs.extend(
            run
            for run in get_json(api, f"{prefix}?{query}")["workflow_runs"]
            if run.get("event") == event
            and run.get("head_branch") == "main"
            and run.get("path") == f".github/workflows/{WORKFLOW}"
            and run.get("conclusion") == "success"
            and str(run.get("updated_at", "")) >= recent
            and (run.get("head_repository") or {}).get("full_name") == repository
        )
    return runs


def _artifact(api: Api, repository: str, run: dict[str, Any]) -> dict[str, Any] | None:
    prefix = f"repos/{repository}/actions"
    run_id, attempt = int(run["id"]), int(run["run_attempt"])
    name = f"linux-test-observations-{attempt}"
    listed = get_json(api, f"{prefix}/runs/{run_id}/artifacts?name={name}")
    artifacts = []
    for artifact in listed["artifacts"]:
        origin = artifact.get("workflow_run") or {}
        if (
            artifact.get("name") == name
            and artifact.get("expired") is False
            and origin.get("id") == run_id
            and origin.get("head_sha") == run["head_sha"]
            and origin.get("head_branch") == "main"
            and 0 < int(artifact.get("size_in_bytes", 0)) <= MAX_BYTES
            and isinstance(artifact.get("created_at"), str)
        ):
            artifacts.append(artifact)
    return artifacts[0] if len(artifacts) == 1 else None


def _download(api: Api, repository: str, artifact: dict[str, Any]) -> bytes | None:
    archive = api(f"repos/{repository}/actions/artifacts/{int(artifact['id'])}/zip")
    if (
        len(archive) > MAX_BYTES
        or artifact.get("digest") != f"sha256:{hashlib.sha256(archive).hexdigest()}"
    ):
        return None
    with zipfile.ZipFile(io.BytesIO(archive)) as files:
        entries = files.infolist()
        if (
            len(entries) != 1
            or entries[0].filename != MEMBER
            or not 0 < entries[0].file_size <= MAX_BYTES
        ):
            return None
        return files.read(entries[0])


def fetch(
    root: Path, repository: str, base: str, output: Path, *, api: Api = gh_api
) -> int | None:
    """Write only a map validated against a successful run and the tested base."""
    output.unlink(missing_ok=True)
    candidate = output.with_suffix(".candidate")
    try:
        artifacts = [
            (artifact, run)
            for run in _trusted_runs(api, repository)
            if (artifact := _artifact(api, repository, run)) is not None
        ]
        # Reruns retain their original workflow run ID and creation time.
        artifacts.sort(
            key=lambda pair: (pair[0]["created_at"], pair[0]["id"]), reverse=True
        )
        for artifact, run in artifacts:
            try:
                data = _download(api, repository, artifact)
                if data is None:
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(data)
                evidence, gap = observation_map(root, candidate, base)
                if gap:
                    continue
                manifest = evidence["manifest"]
                if (
                    manifest["run_id"] == run["id"]
                    and manifest["attempt"] == run["run_attempt"]
                    and manifest["source_sha"] == run["head_sha"]
                    and fresh_observations(root, manifest)
                ):
                    candidate.replace(output)
                    return int(run["id"])
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                zipfile.BadZipFile,
                zipfile.LargeZipFile,
                RuntimeError,
                zlib.error,
                subprocess.SubprocessError,
            ):
                continue
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ):
        pass
    finally:
        candidate.unlink(missing_ok=True)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if (run := fetch(Path.cwd(), args.repository, args.base, args.output)) is None:
        print(
            "::warning::No compatible trusted Linux observation artifact was available."
        )
    else:
        print(f"Restored a compatible Linux observation map from main run {run}.")


if __name__ == "__main__":
    main()
