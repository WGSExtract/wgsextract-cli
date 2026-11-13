"""Issue #776: notes across ideas survive restarts and round-trip together."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "gui/mockups/serve.py"


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "test@example.org")
    git(source, "config", "user.name", "Reviewer")
    mockups = source / "gui/mockups"
    mockups.mkdir(parents=True)
    (mockups / "index.html").write_text('<body data-page-id="index">Main idea</body>')
    (mockups / "serve.py").write_text("never execute this")
    (mockups / "leak.txt").symlink_to("/etc/passwd")
    git(source, "add", ".")
    git(source, "commit", "-m", "main")
    git(source, "checkout", "-b", "mockups/x")
    (mockups / "index.html").write_text('<body data-page-id="index">Idea X</body>')
    (mockups / "review.html").write_text(
        '<body data-page-id="custom-page">Custom</body>'
    )
    git(source, "add", ".")
    git(source, "commit", "-m", "idea")
    bare = tmp_path / "repo.git"
    git(tmp_path, "clone", "--bare", str(source), str(bare))
    return bare


@contextmanager
def running(tmp_path: Path, *args: str) -> Iterator[str]:
    log = tmp_path / "server.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--port", "0", *args],
            stdout=output,
            stderr=output,
        )
        try:
            for _ in range(200):
                match = re.search(r"on (http://127\.0\.0\.1:\d+/)", log.read_text())
                if match:
                    base = match.group(1).rstrip("/")
                    assert request(base, "/api/health")["ok"]
                    yield base
                    return
                if process.poll() is not None:
                    pytest.fail(log.read_text())
                time.sleep(0.05)
            pytest.fail("server never became ready: " + log.read_text())
        finally:
            process.terminate()
            process.wait(timeout=10)


def request(
    base: str, path: str, body: Any = None, headers: dict[str, str] | None = None
) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(req, timeout=10) as response:
        return json.load(response)


def read(base: str, path: str) -> str:
    with urlopen(base + path, timeout=10) as response:
        text: str = response.read().decode()
        return text


def test_776_shared_notes_roundtrip_and_restart(
    tmp_path: Path, bare_repo: Path
) -> None:
    db = tmp_path / "notes.db"
    args = ("--db", str(db), "--repo", str(bare_repo))
    with running(tmp_path, *args) as base:
        assert "Main idea" in read(base, "/")
        assert "Idea X" in read(base, "/preview/x/")
        assert "mockups/x" in read(base, "/preview/")
        assert "All notes" in read(base, "/notes.html")
        for path in ("/serve.py", "/preview/x/serve.py", "/preview/x/leak.txt"):
            with pytest.raises(HTTPError) as error:
                read(base, path)
            assert error.value.code == 404
        for idea in ("main", "mockups/x"):
            path = "/api/feedback/index?idea=" + idea
            doc = request(
                base,
                path,
                {
                    "op": "add",
                    "note": {"id": "nshared", "author": "Randy", "text": idea},
                },
            )
            assert doc["notes"]["nshared"]["text"] == idea
            request(
                base,
                path,
                {
                    "op": "reply",
                    "id": "nshared",
                    "reply": {"id": "rshared", "author": "Mac", "text": "Agreed"},
                },
            )
            request(base, path, {"op": "status", "id": "nshared", "status": "resolved"})
        custom = "/api/feedback/custom-page?idea=mockups/x"
        request(
            base,
            custom,
            {
                "op": "add",
                "note": {"id": "ncustom", "author": "Mac", "text": "Custom page"},
            },
        )
        assert (
            request(base, "/api/notes?page=custom-page")["notes"][0]["url"]
            == "/preview/x/review.html"
        )
        request(base, custom, {"op": "delete", "id": "ncustom"})
        notes = request(base, "/api/notes")["notes"]
        assert {(n["idea"], n["text"], n["status"]) for n in notes} == {
            ("main", "main", "resolved"),
            ("mockups/x", "mockups/x", "resolved"),
        }
        assert all(n["replies"][0]["text"] == "Agreed" for n in notes)
        assert (
            len(
                request(
                    base,
                    "/api/notes?idea=mockups/x&author=Randy&status=resolved&page=index",
                )["notes"]
            )
            == 1
        )
        assert request(base, "/api/notes?status=open")["notes"] == []
        exported = request(base, "/api/export")
        assert exported["about"]
        request(base, "/api/import", exported)
        request(base, "/api/import", exported)
        assert request(base, "/api/export") == exported
        with pytest.raises(HTTPError) as error:
            request(
                base,
                "/api/import",
                {"pages": [{"idea": "main", "page": "../escape", "notes": {}}]},
            )
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            request(
                base, "/api/import", exported, {"Origin": "http://attacker.example"}
            )
        assert error.value.code == 403
        with pytest.raises(HTTPError) as error:
            request(base, "/api/health", headers={"Host": "attacker.example"})
        assert error.value.code == 403
    with running(tmp_path, *args) as base:
        assert request(base, "/api/export") == exported
    dest = tmp_path / "export"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--export-feedback", str(dest)],
        check=True,
        capture_output=True,
    )
    assert (
        json.loads((dest / "index.json").read_text())["notes"]["nshared"]["text"]
        == "main"
    )
    assert (
        json.loads(next((dest / "preview").glob("x-*/index.json")).read_text())[
            "notes"
        ]["nshared"]["text"]
        == "mockups/x"
    )
    imported_db = tmp_path / "imported.db"
    for _ in range(2):
        subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--db",
                str(imported_db),
                "--import-feedback",
                str(dest),
            ],
            check=True,
            capture_output=True,
        )
    with running(tmp_path, "--db", str(imported_db)) as base:
        notes = request(base, "/api/notes")["notes"]
        assert len(notes) == 1
        assert len(notes[0]["replies"]) == 1


def test_776_refreshes_main_and_removes_deleted_ideas(
    tmp_path: Path, bare_repo: Path
) -> None:
    source = bare_repo.parent / "source"
    with running(
        tmp_path,
        "--db",
        str(tmp_path / "notes.db"),
        "--repo",
        str(bare_repo),
        "--refresh-minutes",
        "0.01",
    ) as base:
        git(source, "checkout", "main")
        (source / "gui/mockups/index.html").write_text("Updated main")
        git(source, "add", ".")
        git(source, "commit", "-m", "update")
        git(source, "branch", "-D", "mockups/x")
        for _ in range(100):
            if "Updated main" in read(base, "/") and "mockups/x" not in read(
                base, "/preview/"
            ):
                break
            time.sleep(0.05)
        assert "Updated main" in read(base, "/")
        with pytest.raises(HTTPError) as error:
            read(base, "/preview/x/")
        assert error.value.code == 404


def test_776_colliding_slugs_and_preview_main_never_exchange_notes(
    tmp_path: Path, bare_repo: Path
) -> None:
    source = bare_repo.parent / "source"
    for branch in ("mockups/Foo", "mockups/foo", "mockups/main"):
        git(source, "checkout", "-b", branch, "main")
        (source / "gui/mockups/index.html").write_text(branch)
        shared = source / "gui/mockups/shared/data/jobs.js"
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_text(branch + " application asset")
        git(source, "add", ".")
        git(source, "commit", "-m", branch)
    with running(
        tmp_path,
        "--db",
        str(tmp_path / "notes.db"),
        "--repo",
        str(bare_repo),
        "--refresh-minutes",
        "0.01",
    ) as base:
        assert "Main idea" in read(base, "/")
        assert "mockups/main" in read(base, "/preview/main/")
        for idea in ("main", "mockups/Foo", "mockups/foo", "mockups/main"):
            request(
                base,
                "/api/feedback/index?idea=" + idea,
                {
                    "op": "add",
                    "note": {"id": "ncollision", "author": "Randy", "text": idea},
                },
            )
        time.sleep(1.05)
        git(source, "checkout", "mockups/Foo")
        (source / "gui/mockups/index.html").write_text("Updated Foo")
        git(source, "add", ".")
        git(source, "commit", "-m", "ordinary push reorders colliding previews")
        for _ in range(100):
            if "Updated Foo" in read(base, "/preview/foo/"):
                break
            time.sleep(0.05)
        assert "Updated Foo" in read(base, "/preview/foo/")
        assert (
            read(base, "/preview/foo/shared/data/jobs.js")
            == "mockups/Foo application asset"
        )
        assert (
            read(base, "/preview/foo-2/shared/data/jobs.js")
            == "mockups/foo application asset"
        )
        notes = request(base, "/api/notes")["notes"]
        assert len(notes) == 4
        assert all(n["text"] == n["idea"] for n in notes)
        urls = {n["idea"]: n["url"] for n in notes}
        assert urls["main"] == "/index.html"
        assert urls["mockups/main"] == "/preview/main/index.html"
        assert urls["mockups/Foo"] == "/preview/foo/index.html"
        assert urls["mockups/foo"] == "/preview/foo-2/index.html"


def test_776_online_backup_can_restore_notes(tmp_path: Path) -> None:
    db = tmp_path / "notes.db"
    backups = tmp_path / "backups"
    backups.mkdir()
    for day in range(1, 16):
        (backups / f"notes-200001{day:02d}.db").write_text("old")
    with running(tmp_path, "--db", str(db)) as base:
        request(
            base,
            "/api/feedback/index",
            {
                "op": "add",
                "note": {
                    "id": "nbackup",
                    "author": "Randy",
                    "text": "Survives restore",
                },
            },
        )
        subprocess.run(
            [
                sys.executable,
                str(SCRIPT.parent / "hosting/backup.py"),
                "--root",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
        )
    paths = sorted(backups.glob("notes-*.db"))
    assert len(paths) == 14
    assert not (backups / "notes-20000101.db").exists()
    with running(tmp_path, "--db", str(paths[-1])) as base:
        assert request(base, "/api/notes")["notes"][0]["text"] == "Survives restore"


def test_776_file_mode_still_writes_repo_feedback(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback"
    with running(tmp_path, "--feedback-dir", str(feedback)) as base:
        request(
            base,
            "/api/feedback/index",
            {
                "op": "add",
                "note": {"id": "nfile", "author": "Randy", "text": "File mode"},
            },
        )
    saved = json.loads((feedback / "index.json").read_text())
    assert saved["notes"]["nfile"]["text"] == "File mode"
    assert saved["about"]
    with running(tmp_path, "--feedback-dir", str(feedback)) as base:
        assert request(base, "/api/feedback/index")["notes"] == saved["notes"]


def test_776_public_peer_gets_403_even_with_forwarded_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    from review_http import ReviewFiles

    class PublicPeerServer(ThreadingHTTPServer):
        def get_request(self) -> Any:
            sock, (_, port) = super().get_request()
            return sock, ("8.8.8.8", port)

    server = PublicPeerServer(("127.0.0.1", 0), ReviewFiles)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with pytest.raises(HTTPError) as error:
            request(
                f"http://127.0.0.1:{server.server_port}",
                "/api/health",
                headers={"X-Forwarded-For": "127.0.0.1"},
            )
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    ("address", "allowed"),
    [
        ("10.1.10.48", True),
        ("172.16.0.1", True),
        ("192.168.1.1", True),
        ("100.102.38.43", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("fd7a:115c:a1e0::1", True),
        ("::ffff:10.1.10.48", True),
        ("8.8.8.8", False),
        ("169.254.1.1", False),
        ("100.128.0.1", False),
        ("2001:4860:4860::8888", False),
        ("not-an-address", False),
    ],
)
def test_776_private_network_boundary(
    monkeypatch: pytest.MonkeyPatch,
    address: str,
    allowed: bool,
) -> None:
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    from review_access import allowed_client

    assert allowed_client(address) is allowed
