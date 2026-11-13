"""Real Git pushes to the isolated local review namespace refresh within seconds."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError

import pytest
from test_mockup_review_server import SCRIPT, git, read, request, running
from test_mockup_review_server import bare_repo as bare_repo


def test_776_local_push_refreshes_without_github_and_retains_deleted_idea_notes(
    tmp_path: Path, bare_repo: Path
) -> None:
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT.parent / "hosting/local_repo.py"),
            "--root",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
    )
    remote, local_repo = bare_repo.parent / "source", tmp_path / "local.git"
    source = tmp_path / "dev"
    git(
        tmp_path,
        "clone",
        "--depth=1",
        "--branch",
        "mockups/x",
        remote.as_uri(),
        str(source),
    )
    git(source, "config", "user.email", "test@example.org")
    git(source, "config", "user.name", "Reviewer")
    with running(
        tmp_path,
        "--db",
        str(tmp_path / "notes.db"),
        "--repo",
        str(bare_repo),
        "--local-repo",
        str(local_repo),
    ) as base:
        git(
            tmp_path,
            "--git-dir",
            str(bare_repo),
            "remote",
            "set-url",
            "origin",
            "http://127.0.0.1:1/unreachable.git",
        )
        git(source, "checkout", "-b", "work/draft")
        (source / "gui/mockups/index.html").write_text("Local-only unpushed change")
        git(source, "add", ".")
        git(source, "commit", "-m", "unpublished idea")
        subprocess.run(
            ["git", "-C", str(source), "push", str(local_repo), "HEAD:x"],
            check=True,
            capture_output=True,
        )
        for _ in range(100):
            try:
                if "Local-only unpushed change" in read(base, "/local/x/"):
                    break
            except HTTPError:
                pass
            time.sleep(0.05)
        assert "Local-only unpushed change" in read(base, "/local/x/")
        assert "Idea X" in read(base, "/preview/x/")
        assert '"local_push": true' in read(base, "/local/x/shared/version.js")
        assert '"hosted": true' in read(base, "/local/x/shared/version.js")
        request(
            base,
            "/api/feedback/index?idea=local/x",
            {
                "op": "add",
                "note": {"id": "nlocalpush", "author": "Randy", "text": "Local review"},
            },
        )
        assert (
            request(base, "/api/notes?idea=local/x")["notes"][0]["url"]
            == "/local/x/index.html"
        )
        git(remote, "checkout", "main")
        (remote / "gui/mockups/index.html").write_text("Refreshed GitHub main")
        git(remote, "add", ".")
        git(remote, "commit", "-m", "GitHub refresh")
        git(
            tmp_path,
            "--git-dir",
            str(bare_repo),
            "remote",
            "set-url",
            "origin",
            str(remote),
        )
        assert request(base, "/api/refresh", {})["queued"]
        for _ in range(100):
            if "Refreshed GitHub main" in read(base, "/"):
                break
            time.sleep(0.05)
        assert "Refreshed GitHub main" in read(base, "/")
        assert "Local-only unpushed change" in read(base, "/local/x/")
        assert "Idea X" in read(base, "/preview/x/")
        subprocess.run(
            ["git", "-C", str(source), "push", str(local_repo), ":x"],
            check=True,
            capture_output=True,
        )
        for _ in range(100):
            try:
                read(base, "/local/x/")
            except HTTPError as error:
                if error.code == 404:
                    break
            time.sleep(0.05)
        with pytest.raises(HTTPError) as missing:
            read(base, "/local/x/")
        assert missing.value.code == 404
        assert (
            request(base, "/api/notes?idea=local/x")["notes"][0]["text"]
            == "Local review"
        )
        assert any(
            p["idea"] == "local/x" for p in request(base, "/api/export")["pages"]
        )
