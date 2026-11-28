"""Regression coverage for transient failures in the CI URL audit (#1692)."""

import secrets
import socket
import struct
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from ftplib import error_perm
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from ssl import SSLCertVerificationError, SSLEOFError
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from scripts import check_remote_resources as checks
from tests.http_server import DownloadHTTPServer


@dataclass(frozen=True)
class Reply:
    status: int | None = 206
    retry_after: str | None = None
    length: int | None = 1
    body: bytes = b"x"
    chunked: bool = False
    content_range: str | None = None


Endpoint = tuple[str, list[Reply], list[str]]


@pytest.fixture
def endpoint() -> Iterator[Endpoint]:
    path = f"/{secrets.token_hex(8)}/resource"
    replies: list[Reply] = []
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            if self.path != path:
                self.send_error(404)
                return
            requests.append(self.path)
            reply = replies.pop(0) if replies else Reply()
            if reply.status is None:
                # Abort the connection with a reset rather than a graceful EOF.
                linger = struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0)
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
                self.connection.close()
                return
            self.send_response(reply.status)
            if reply.retry_after is not None:
                self.send_header("Retry-After", reply.retry_after)
            if reply.chunked:
                self.send_header("Transfer-Encoding", "chunked")
            if reply.length is not None:
                self.send_header("Content-Length", str(reply.length))
            if reply.content_range is not None:
                self.send_header("Content-Range", reply.content_range)
            self.end_headers()
            self.wfile.write(reply.body)

    with DownloadHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(
            target=server.serve_forever, args=(0.01,), daemon=True
        )
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}{path}", replies, requests
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize(
    ("replies_to_send", "expected_delays", "result"),
    [
        ([Reply(503)], [5], 0),
        ([Reply(429, "12"), Reply(502, "Thu, 01 Jan 1970 00:00:30 GMT")], [12, 30], 0),
        ([Reply(404, "12")], [], 1),
        ([Reply(410)], [], 1),
        ([Reply(None)] * 4, [5, 20], 1),
    ],
    ids=["recovered", "retry-after", "not-found", "gone", "persistent-reset"],
)
def test_1692_url_audit_retries_only_transient_failures(
    endpoint: Endpoint,
    replies_to_send: list[Reply],
    expected_delays: list[float],
    result: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url, replies, requests = endpoint
    replies.extend(replies_to_send)
    slept: list[float] = []
    monkeypatch.setattr(
        checks, "time", SimpleNamespace(sleep=slept.append, time=lambda: 0)
    )
    monkeypatch.setattr(checks, "snapshot", lambda _: {url: {}})

    assert checks.main(["--out", str(tmp_path / "urls.json")]) == result

    attempts = len(expected_delays) + 1
    assert len(requests) == attempts
    assert slept == expected_delays
    output = capsys.readouterr().out
    if result:
        assert f"FAILED {url}" in output
    else:
        assert f"OK {url} (after {attempts} attempts)" in output


@pytest.mark.parametrize(
    "reply",
    [
        Reply(body=b""),
        Reply(length=None, body=b"", chunked=True),
        Reply(length=None, body=b"", content_range="bytes 0-0/2"),
    ],
    ids=["content-length", "chunked", "content-range"],
)
def test_1692_closed_connection_before_first_byte_recovers(
    endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch, reply: Reply
) -> None:
    url, replies, requests = endpoint
    replies.append(reply)
    slept: list[float] = []
    monkeypatch.setattr(checks, "time", SimpleNamespace(sleep=slept.append))

    assert checks.check_url(url, {}) == 2
    assert len(requests) == 2
    assert slept == [5]


@pytest.mark.parametrize(
    ("reply", "failure"),
    [
        (Reply(202, body=b""), "HTTP 202"),
        (Reply(length=0, body=b""), "empty response"),
        (Reply(503, "1000000000000"), "Retry-After"),
    ],
)
def test_1692_definitive_response_or_excessive_retry_after_fails_immediately(
    endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch, reply: Reply, failure: str
) -> None:
    url, replies, requests = endpoint
    replies.append(reply)
    slept: list[float] = []
    monkeypatch.setattr(checks, "time", SimpleNamespace(sleep=slept.append))

    with pytest.raises(ValueError, match=failure):
        checks.check_url(url, {})
    assert len(requests) == 1
    assert not slept


@pytest.mark.parametrize(
    ("reason", "expected_delays"),
    [
        (SSLEOFError("closed during TLS handshake"), [5, 20]),
        (SSLCertVerificationError("untrusted certificate"), []),
    ],
)
def test_1692_tls_disconnect_retries_but_certificate_failure_does_not(
    reason: OSError, expected_delays: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(checks, "time", SimpleNamespace(sleep=slept.append))
    monkeypatch.setattr(checks, "urlopen", Mock(side_effect=URLError(reason)))

    with pytest.raises(URLError):
        checks.check_url("https://127.0.0.1:1/resource", {})
    assert slept == expected_delays


@pytest.mark.parametrize(
    ("reason", "ftp", "expected_delays"),
    [
        (socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure"), False, [5, 20]),
        (socket.gaierror(socket.EAI_NONAME, "unknown host"), False, []),
        (ConnectionRefusedError("connection refused"), True, [5, 20]),
        (EOFError(), True, [5, 20]),
        (error_perm("550 File unavailable"), True, []),
    ],
)
def test_1692_only_temporary_dns_and_ftp_transport_failures_retry(
    reason: Exception,
    ftp: bool,
    expected_delays: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(checks, "time", SimpleNamespace(sleep=slept.append))
    error = URLError(f"ftp error: {reason}" if ftp else reason)
    if ftp:
        error.__cause__ = reason
    monkeypatch.setattr(checks, "urlopen", Mock(side_effect=error))

    scheme = "ftp" if ftp else "https"
    with pytest.raises(URLError):
        checks.check_url(f"{scheme}://127.0.0.1:1/resource", {})
    assert slept == expected_delays
