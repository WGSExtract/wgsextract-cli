import hashlib
import json
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from tests.http_server import DownloadHTTPServer, serving
from wgsextract_cli.core.download_progress import DownloadCancelled
from wgsextract_cli.core.ref_library import downloads, segmented
from wgsextract_cli.core.ref_library.segmented import ranged_revision

DATA = os.urandom(10_000)
ETAG = f'"{hashlib.md5(DATA).hexdigest()}"'  # as tests.http_server sends it
REVISION = segmented.Revision(len(DATA), ETAG)


class _Server:
    """Serves DATA with byte ranges; can cut responses short or ignore ranges."""

    def __init__(self):
        self.drop = {}  # piece start -> times left to cut a response in it short
        self.busy = {}  # piece start -> times left to answer it with a status
        self.ignore_ranges = False
        # (start, end) of each GET, logged before answering it: the client
        # can't finish a request before its entry exists, whereas a byte count
        # bumped after the write can land after the client has moved on.
        self.requests: list[tuple[int, int]] = []
        self.path = f"/{secrets.token_hex(8)}/big.bin"
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _foreign(self) -> bool:
                """A request from other software probing the port (#987)."""
                if self.path == server.path:
                    return False
                self.send_error(404)
                return True

            def do_HEAD(self):
                if self._foreign():
                    return
                self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(DATA)))
                self.send_header("ETag", ETAG)
                self.end_headers()

            def do_GET(self):
                if self._foreign():
                    return
                spec = self.headers.get("Range")
                if not spec or server.ignore_ranges:
                    server.requests.append((0, len(DATA)))
                    body, code = DATA, 200
                else:
                    first, _, last = spec.removeprefix("bytes=").partition("-")
                    start = int(first)
                    end = int(last) + 1 if last else len(DATA)
                    server.requests.append((start, end))
                    body, code = DATA[start:end], 206
                    piece = start - start % 1000
                    if server.busy.get(piece):
                        server.busy[piece] -= 1
                        self.send_error(503 if piece else 404)
                        return
                cut = code == 206 and server.drop.get(piece)
                if cut:
                    server.drop[piece] -= 1
                self.send_response(code)
                self.send_header("ETag", ETAG)
                if code == 206:
                    last = start + len(body) - 1
                    self.send_header(
                        "Content-Range", f"bytes {start}-{last}/{len(DATA)}"
                    )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if cut:
                    self.wfile.write(body[: len(body) // 2])
                    self.close_connection = True
                    return
                self.wfile.write(body)

        self.httpd = DownloadHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}{self.path}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def server(monkeypatch):
    srv = _Server()
    monkeypatch.setattr(segmented, "ranged_revision", ranged_revision)
    monkeypatch.setattr(segmented, "SEGMENTED_MIN_SIZE", 1)
    monkeypatch.setattr(segmented, "SEGMENT_SIZE", 1000)
    monkeypatch.setattr(segmented, "_backoff", lambda attempt: 0)
    monkeypatch.setattr(
        downloads, "resolve_github_release_asset_sha256", lambda u: None
    )
    monkeypatch.setattr(downloads, "restore_cached_download", lambda *a, **k: False)
    monkeypatch.setattr(downloads, "store_download_in_dev_cache", lambda *a, **k: None)
    yield srv
    srv.httpd.shutdown()


def test_large_file_comes_in_pieces(server, tmp_path):
    dest = tmp_path / "big.bin"

    assert downloads.download_file(server.url, str(dest))

    assert dest.read_bytes() == DATA
    assert sorted(server.requests) == [(s, s + 1000) for s in range(0, len(DATA), 1000)]
    assert not (tmp_path / "big.bin.partial.segments").exists()


def test_a_cut_off_piece_is_resumed(server, tmp_path):
    server.drop = {0: 1, 4000: 2, 9000: 1}
    dest = tmp_path / "big.bin"

    assert downloads.download_file(server.url, str(dest))

    assert dest.read_bytes() == DATA


def test_a_piece_the_server_is_too_busy_for_is_retried(server, tmp_path):
    server.busy = {3000: 2}
    dest = tmp_path / "big.bin"

    assert downloads.download_file(server.url, str(dest))

    assert dest.read_bytes() == DATA


def test_a_piece_the_server_refuses_is_not_retried(server, tmp_path):
    server.busy = {0: 1}
    dest = tmp_path / "big.bin"

    assert not downloads.download_file(server.url, str(dest))
    assert server.busy == {0: 0}


def test_a_failed_download_resumes_on_the_next_run(server, tmp_path, monkeypatch):
    monkeypatch.setattr(segmented, "ATTEMPTS", 2)
    server.drop = {5000: 99}
    dest = tmp_path / "big.bin"

    assert not downloads.download_file(server.url, str(dest))
    assert not dest.exists()
    assert (tmp_path / "big.bin.partial.segments").exists()

    # What the state counts as received is on disk, byte for byte.
    state = json.loads((tmp_path / "big.bin.partial.segments").read_text())
    on_disk = (tmp_path / "big.bin.partial").read_bytes()
    for start, count in state["received"].items():
        piece = slice(int(start), int(start) + count)
        assert on_disk[piece] == DATA[piece]
    assert state["received"]["5000"] < 1000

    server.drop = {}
    server.requests = []
    assert downloads.download_file(server.url, str(dest))
    assert dest.read_bytes() == DATA
    # Only what hadn't arrived is asked for again, each byte once.
    missing = {
        i
        for start in range(0, len(DATA), 1000)
        for i in range(start + state["received"][str(start)], start + 1000)
    }
    asked = [i for start, end in server.requests for i in range(start, end)]
    assert sorted(asked) == sorted(missing)


def test_a_server_that_ignores_ranges_gets_one_connection(server, tmp_path):
    server.ignore_ranges = True
    dest = tmp_path / "big.bin"

    assert downloads.download_file(server.url, str(dest))

    assert dest.read_bytes() == DATA
    assert not (tmp_path / "big.bin.partial.segments").exists()


def test_1210_segment_state_and_retry_log_do_not_keep_signed_url(
    tmp_path, monkeypatch, caplog
):
    with serving(DATA) as srv:
        srv.etags = True
        srv.get_errors["hs38.fa.gz"] = [503, 503]
        monkeypatch.setattr(segmented, "ATTEMPTS", 2)
        dest = tmp_path / "hs38.fa.gz"
        signed_url = (
            f"{srv.url}?redirect=https://example.test/session&sig=fixture-signature"
            "#fixture-fragment"
        )

        with (
            caplog.at_level(logging.DEBUG),
            pytest.raises(OSError, match="503") as failure,
        ):
            segmented.download_segmented(signed_url, str(dest), REVISION)

    state = (tmp_path / "hs38.fa.gz.partial.segments").read_text()
    assert srv.url in caplog.text and "Retrying" in caplog.text
    for text in (state, caplog.text, str(failure.value)):
        assert "fixture-signature" not in text and "fixture-fragment" not in text
    assert srv.url not in state


@pytest.mark.parametrize(
    "damage",
    [
        "array",
        "legacy",
        "missing-offset",
        "bad-count",
        "changed-url",
        "short",
        "no-head",
    ],
)
def test_1210_unusable_segment_state_restarts_download(tmp_path, damage, monkeypatch):
    monkeypatch.setattr(segmented, "ranged_revision", ranged_revision)
    monkeypatch.setattr(segmented, "SEGMENT_SIZE", 1000)
    dest = tmp_path / "hs38.fa.gz"
    partial = tmp_path / "hs38.fa.gz.partial"
    state_path = tmp_path / "hs38.fa.gz.partial.segments"
    partial.write_bytes(b"x" * len(DATA))
    with serving(DATA) as srv:
        srv.etags = True
        url = f"{srv.url}?sig=old-signature"
        counts = {str(start): 1000 for start in range(0, len(DATA), 1000)}
        state = {
            "url_sha256": hashlib.sha256(url.encode()).hexdigest(),
            "size": len(DATA),
            "etag": ETAG,
            "received": counts,
        }
        if damage == "legacy":
            del state["url_sha256"]
            state["url"] = url
        elif damage == "missing-offset":
            counts["1"] = counts.pop("0")
        elif damage in {"bad-count", "no-head"}:
            counts["0"] = -1
        elif damage == "changed-url":
            url = f"{srv.url}?sig=new-signature"
        elif damage == "short":
            partial.write_bytes(b"short")
        state_path.write_text(json.dumps([] if damage == "array" else state))
        if damage == "no-head":
            srv.head_status = 405

        assert downloads.download_file(
            url, str(dest), progress_callback=lambda *_: None, use_cache=False
        )
        if damage == "no-head":
            assert srv.gets() == [None]
    assert dest.read_bytes() == DATA


@pytest.mark.parametrize("cancelled", [False, True], ids=["ranges", "cancel"])
def test_1210_state_write_failure_preserves_segmented_error(
    tmp_path, caplog, cancelled
):
    dest = tmp_path / "hs38.fa.gz"
    (tmp_path / "hs38.fa.gz.partial.segments.tmp").mkdir()
    cancel = threading.Event()
    with serving(DATA) as srv:
        srv.etags = True
        if cancelled:
            cancel.set()
        else:
            srv.ignore_ranges = True
        kind = DownloadCancelled if cancelled else segmented.RangesUnsupported

        with pytest.raises(kind) as failure:
            segmented.download_segmented(
                f"{srv.url}?sig=fixture-signature",
                str(dest),
                REVISION,
                cancel_event=cancel,
            )

    assert "fixture-signature" not in str(failure.value)
    assert "Could not save download progress" in caplog.text


def test_1210_failed_segment_checkpoint_cannot_resume_sparse_file_as_single(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(segmented, "ranged_revision", ranged_revision)
    monkeypatch.setattr(segmented, "SEGMENTED_MIN_SIZE", 1)
    monkeypatch.setattr(segmented, "SEGMENT_SIZE", 1000)
    dest = tmp_path / "hs38.fa.gz"
    blocked = tmp_path / "hs38.fa.gz.partial.segments.tmp"
    blocked.mkdir()
    with serving(DATA) as srv:
        srv.etags = True
        # A single-connection failure can leave a source marker without data.
        srv.head_status = 405
        srv.get_status = 404
        assert not downloads.download_file(
            srv.url, str(dest), progress_callback=lambda *_: None, use_cache=False
        )
        # With ranges available on the next run, the failed transfer and
        # blocked checkpoint leave a full-size sparse file without state.
        srv.head_status = None
        assert not downloads.download_file(srv.url, str(dest), use_cache=False)
        assert (tmp_path / "hs38.fa.gz.partial").exists()
        blocked.rmdir()
        srv.get_status = None
        srv.head_status = 405

        assert downloads.download_file(
            srv.url, str(dest), progress_callback=lambda *_: None, use_cache=False
        )
    assert dest.read_bytes() == DATA
