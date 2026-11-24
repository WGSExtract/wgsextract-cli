"""Trusted Linux maps survive cache eviction without enabling untrusted maps."""

import gzip
import hashlib
import io
import json
import platform
import subprocess
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.ci_impact_fetch import fetch
from scripts.ci_impact_observations import SCHEMA


@pytest.mark.parametrize(
    "newest",
    [
        "compatible",
        "older-rerun",
        "wrong-lock",
        "wrong-attempt",
        "stale",
        "bad-digest",
        "untrusted",
        "extra-member",
        "expired",
        "missing",
        "no-trusted-runs",
    ],
)
def test_1482_restores_only_a_compatible_trusted_main_artifact(
    tmp_path: Path, newest: str
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            [
                "git",
                "-c",
                "core.hooksPath=",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.email=test@example.test",
                "-c",
                "user.name=Test",
                *args,
            ],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init", "-qb", "main")
    (tmp_path / "pixi.lock").write_bytes(b"lock")
    git("add", "pixi.lock")
    git("commit", "-qm", "Observation source")
    source = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-qm", "Tested base")
    base = git("rev-parse", "HEAD")

    repository = "owner/repo"
    digest = hashlib.sha256(b"lock").hexdigest()
    now = datetime.now(timezone.utc)

    def archive(run: int, variation: str) -> bytes:
        manifest = {
            "source_sha": source,
            "schema": SCHEMA,
            "platform": "Linux",
            "machine": "x86_64",
            "python": platform.python_version(),
            "profile": "ubuntu-x64/all/skip-independent=false",
            "run_id": run,
            "attempt": 1 if variation == "wrong-attempt" else 2,
            "lock_sha256": "0" * 64 if variation == "wrong-lock" else digest,
            "recorded_at": (
                now - timedelta(hours=49 if variation == "stale" else 0)
            ).isoformat(),
        }
        data = {"manifest": manifest, "observed_tests": ["tests/test_map.py"]}
        data.update(
            dict.fromkeys(("modules", "functions", "symbol_reads", "seconds"), {})
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as files:
            files.writestr("linux.json.gz", gzip.compress(json.dumps(data).encode()))
            if variation == "extra-member":
                files.writestr("../outside", b"do not unpack")
        return buffer.getvalue()

    archives = {1: archive(1, "compatible"), 2: archive(2, newest)}

    def api(endpoint: str) -> bytes:
        path, query = urlsplit(endpoint).path, parse_qs(urlsplit(endpoint).query)
        if path.endswith("/actions/workflows/ubuntu_ci.yml/runs"):
            event = query["event"][0]
            run = {"schedule": 1, "workflow_dispatch": 2}[event]
            rerun = run == 1 and newest == "older-rerun"
            original = now - timedelta(days=5 if rerun else 1)
            updated = now - timedelta(minutes=0 if rerun else 10 if run == 2 else 20)
            skip_old = rerun and "created" in query
            return json.dumps(
                {
                    "workflow_runs": []
                    if newest == "no-trusted-runs" or skip_old
                    else [
                        {
                            "id": run,
                            "run_attempt": 2,
                            "head_sha": source,
                            "head_branch": "topic"
                            if run == 2 and newest == "untrusted"
                            else "main",
                            "head_repository": {"full_name": repository},
                            "path": ".github/workflows/ubuntu_ci.yml",
                            "event": event,
                            "conclusion": "success",
                            "created_at": original.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "updated_at": updated.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }
                    ]
                }
            ).encode()
        if path.endswith("/artifacts"):
            run = int(path.split("/")[-2])
            if run == 2 and newest == "untrusted":
                pytest.fail("Untrusted branch must not be queried for artifacts")
            return json.dumps(
                {
                    "artifacts": []
                    if run == 2 and newest == "missing"
                    else [
                        {
                            "id": run,
                            "name": "linux-test-observations-2",
                            "expired": run == 2 and newest == "expired",
                            "created_at": (
                                now
                                - timedelta(
                                    minutes=0
                                    if newest == "older-rerun" and run == 1
                                    else 11
                                    if run == 2
                                    else 21
                                )
                            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "digest": "sha256:"
                            + (
                                "0" * 64
                                if run == 2 and newest == "bad-digest"
                                else hashlib.sha256(archives[run]).hexdigest()
                            ),
                            "size_in_bytes": len(archives[run]),
                            "workflow_run": {
                                "id": run,
                                "head_sha": source,
                                "head_branch": "main",
                            },
                        }
                    ]
                }
            ).encode()
        if path.endswith("/zip"):
            return archives[int(path.split("/")[-2])]
        raise AssertionError(endpoint)

    output = tmp_path / "linux.json.gz"
    output.write_bytes(b"previous output was not verified for this request")
    selected_run = fetch(tmp_path, repository, base, output, api=api)
    if newest == "no-trusted-runs":
        assert selected_run is None and not output.exists()
    else:
        expected = 2 if newest == "compatible" else 1
        assert selected_run == expected
        with gzip.open(output, "rt") as stream:
            assert json.load(stream)["manifest"]["run_id"] == expected
    assert not (tmp_path / "outside").exists()
