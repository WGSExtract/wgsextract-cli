"""Behaviour regressions from the hosted-review PR feedback."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest
from test_build_mockup_previews import build as build_pages
from test_mockup_review_server import bare_repo as bare_repo
from test_mockup_review_server import git, read, request, running


def test_776_cache_pruning_preserves_unrelated_outputs(
    tmp_path: Path, bare_repo: Path
) -> None:
    other = tmp_path / "other-job"
    (other / "shared").mkdir(parents=True)
    (other / "shared/version.js").write_text(
        'window.WGSM_VERSION = {"branch":"main"};\n'
    )
    (other / "keep.txt").write_text("unrelated work")
    source = bare_repo.parent / "source"
    with running(
        tmp_path,
        "--db",
        str(tmp_path / "notes.db"),
        "--repo",
        str(bare_repo),
        "--cache",
        str(tmp_path),
    ) as base:
        git(source, "checkout", "main")
        for revision in range(2):
            text = f"Updated {revision}"
            (source / "gui/mockups/index.html").write_text(text)
            git(source, "add", ".")
            git(source, "commit", "-m", text)
            request(base, "/api/refresh", {})
            for _ in range(100):
                if text in read(base, "/"):
                    break
                time.sleep(0.05)
            assert text in read(base, "/")
        assert (other / "keep.txt").read_text() == "unrelated work"
        assert (other / "shared/version.js").is_file()
        assert (bare_repo / "HEAD").is_file()


def test_776_hosted_ideas_are_not_limited_to_pages_preview_count(
    tmp_path: Path, bare_repo: Path
) -> None:
    source = bare_repo.parent / "source"
    for i in range(21):
        git(source, "branch", f"mockups/zz-{i:02}", "main")
    with running(
        tmp_path, "--db", str(tmp_path / "notes.db"), "--repo", str(bare_repo)
    ) as base:
        assert "Main idea" in read(base, "/preview/zz-20/")
        request(
            base,
            "/api/feedback/index?idea=mockups/zz-20",
            {
                "op": "add",
                "note": {"id": "nlast", "author": "Randy", "text": "Last idea"},
            },
        )
        assert (
            request(base, "/api/notes?idea=mockups/zz-20")["notes"][0]["url"]
            == "/preview/zz-20/index.html"
        )
    published = build_pages(tmp_path, source)
    assert len([p for p in published.iterdir() if p.is_dir()]) == 20


def test_776_non_utf8_page_does_not_break_shared_exports(
    tmp_path: Path, bare_repo: Path
) -> None:
    source = bare_repo.parent / "source"
    (source / "gui/mockups/legacy.html").write_bytes(
        b'<body data-page-id="legacy">caf\xe9</body>'
    )
    git(source, "add", ".")
    git(source, "commit", "-m", "non-UTF8 mockup")
    with running(
        tmp_path, "--db", str(tmp_path / "notes.db"), "--repo", str(bare_repo)
    ) as base:
        request(
            base,
            "/api/feedback/legacy?idea=mockups/x",
            {
                "op": "add",
                "note": {"id": "nlatin", "author": "Randy", "text": "Retain me"},
            },
        )
        assert (
            request(base, "/api/notes")["notes"][0]["url"] == "/preview/x/legacy.html"
        )
        assert (
            request(base, "/api/export")["pages"][0]["notes"]["nlatin"]["text"]
            == "Retain me"
        )
        assert "legacy" in read(base, "/preview/x/legacy.html")


def test_776_static_hosts_are_validated_and_directory_urls_redirect(
    tmp_path: Path, bare_repo: Path
) -> None:
    with running(
        tmp_path, "--db", str(tmp_path / "notes.db"), "--repo", str(bare_repo)
    ) as base:
        with pytest.raises(HTTPError) as denied:
            urlopen(
                Request(base + "/", headers={"Host": "unsupported.invalid"}), timeout=5
            )
        assert denied.value.code == 403
        for path in ("/preview", "/preview/x", "/local"):
            with urlopen(base + path + "?theme=dark", timeout=5) as response:
                assert response.geturl().endswith(path + "/?theme=dark")


def test_776_file_summary_identifies_conflicts_without_hiding_healthy_pages(
    tmp_path: Path,
) -> None:
    feedback = tmp_path / "feedback"
    with running(tmp_path, "--feedback-dir", str(feedback)) as base:
        request(
            base,
            "/api/feedback/index",
            {
                "op": "add",
                "note": {"id": "nhealthy", "author": "Randy", "text": "Healthy"},
            },
        )
        (feedback / "broken.json").write_text("<<<<<<< HEAD\n")
        pages = {p["page"]: p for p in request(base, "/api/feedback")["pages"]}
        assert pages["index"]["open"] == 1
        assert pages["broken"]["error"]
        with pytest.raises(HTTPError) as conflict:
            request(base, "/api/feedback/broken")
        assert conflict.value.code == 409


def test_776_large_import_roundtrips_and_slow_export_does_not_block_writes(
    tmp_path: Path,
) -> None:
    notes = {
        f"nbulk{i:04}": {"id": f"nbulk{i:04}", "author": "Randy", "text": "x" * 8000}
        for i in range(700)
    }
    doc = {"pages": [{"idea": "main", "page": "index", "notes": notes}]}
    assert len(json.dumps(doc).encode()) > 512 * 1024
    with running(tmp_path, "--db", str(tmp_path / "notes.db")) as base:
        request(base, "/api/import", doc)
        exported = request(base, "/api/export")
        request(base, "/api/import", exported)
        assert len(request(base, "/api/notes")["notes"]) == 700
        with socket.create_connection(
            ("127.0.0.1", urlsplit(base).port or 0), timeout=5
        ) as slow:
            slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            slow.sendall(
                (
                    "GET /api/export HTTP/1.1\r\nHost: "
                    + base.removeprefix("http://")
                    + "\r\n\r\n"
                ).encode()
            )
            header = b""
            while not header.endswith(b"\r\n\r\n"):
                part = slow.recv(1)
                assert part
                header += part
            # Leave the body unread so the export eventually blocks its socket.
            change = Request(
                base + "/api/feedback/index",
                data=json.dumps(
                    {"op": "status", "id": "nbulk0000", "status": "resolved"}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(change, timeout=5) as response:
                assert json.load(response)["notes"]["nbulk0000"]["status"] == "resolved"
