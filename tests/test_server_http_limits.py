"""#1173 review: bounded peers, offline binds and uncorrupted range responses."""

import errno
import http.client
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import BinaryIO, cast
from urllib.parse import urlencode, urlsplit

import psutil
import pytest

from tests.smoke_utils import get_cli_command, state_directory_setting
from tests.test_server_remote_viewer import (
    HOST,
    remote_server,
    request,
    requires_safe_reads,
)
from wgsextract_cli.core.config import get_config_path
from wgsextract_cli.server.http_api import Server
from wgsextract_cli.server.http_limits import LimitedHTTPServer
from wgsextract_cli.server.jobs import JobManager
from wgsextract_cli.server.operations import PathGuard


def test_1173_review_unauthenticated_idle_peers_have_bounded_admission(
    tmp_path: Path,
) -> None:
    with remote_server(
        tmp_path, enabled=False, max_connections=1, header_timeout_seconds=1
    ) as server:
        with socket.create_connection(
            ("127.0.0.1", server.server_port), timeout=3
        ) as idle:
            with socket.create_connection(
                ("127.0.0.1", server.server_port), timeout=0.3
            ) as excess:
                assert excess.recv(1) == b""
            assert idle.recv(1) == b""
        deadline = time.monotonic() + 3
        while True:
            try:
                status, _, body = request(server, "/v1/health")
                if status == 200:
                    assert json.loads(body)["ok"]
                    break
            except (ConnectionError, http.client.RemoteDisconnected):
                pass
            assert time.monotonic() < deadline


def test_1173_review_header_deadline_expires_despite_dripped_bytes(
    tmp_path: Path,
) -> None:
    with remote_server(tmp_path, enabled=False, header_timeout_seconds=0.3) as server:
        with socket.create_connection(
            ("127.0.0.1", server.server_port), timeout=3
        ) as peer:
            peer.sendall(b"GET /v1/health HTTP/1.1\r\nX-Trace: ")
            stop = threading.Event()

            def drip() -> None:
                while not stop.wait(0.03):
                    try:
                        peer.sendall(b"x")
                    except OSError:
                        break

            sender = threading.Thread(target=drip)
            sender.start()
            try:
                try:
                    assert peer.recv(1) == b""
                except ConnectionResetError:
                    # Aborting an incomplete request can reset TCP when the
                    # peer is still sending. Both reset and EOF end the read.
                    pass
            finally:
                stop.set()
                sender.join(3)


@requires_safe_reads
def test_1173_review_header_deadline_does_not_interrupt_authenticated_stream(
    tmp_path: Path,
) -> None:
    file = tmp_path / "large"
    size = 33 * 1024 * 1024
    with file.open("wb") as output:
        output.truncate(size)
    with remote_server(tmp_path, header_timeout_seconds=0.3) as server:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        try:
            connection.request(
                "GET",
                "/v1/viewer-file?" + urlencode({"path": str(file)}),
                headers={
                    "Host": HOST,
                    "Authorization": f"Bearer {server.token}",
                    "Range": f"bytes=0-{size - 1}",
                },
            )
            response = connection.getresponse()
            assert response.status == 206
            time.sleep(0.9)
            data = response.read()
            assert len(data) == size and data.count(0) == size
        finally:
            connection.close()


def test_1173_review_numeric_bind_never_attempts_reverse_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny_dns(*args: object) -> str:
        raise AssertionError("numeric binding attempted reverse DNS")

    monkeypatch.setattr(socket, "getfqdn", deny_dns)
    with remote_server(tmp_path, enabled=False) as server:
        assert request(server, "/v1/health")[0] == 200


@pytest.mark.parametrize("authorized,status", [(False, 401), (True, 400)])
def test_1173_review_continue_is_followed_by_final_error_response(
    tmp_path: Path, authorized: bool, status: int
) -> None:
    with remote_server(tmp_path, enabled=False) as server:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        headers = {
            "Host": HOST,
            "Expect": "100-continue",
            "Content-Length": "0",
            "Content-Type": "application/json",
        }
        if authorized:
            headers["Authorization"] = f"Bearer {server.token}"
        try:
            connection.request("POST", "/v1/ops/info", headers=headers)
            response = connection.getresponse()
            assert response.status == status
            assert "error" in json.loads(response.read())
        finally:
            connection.close()


def test_1173_review_loopback_rejects_public_advertisement(tmp_path: Path) -> None:
    manager = JobManager(tmp_path / "jobs", config_path=get_config_path())
    try:
        with pytest.raises(ValueError, match="non-loopback"):
            Server(
                0,
                "token",
                manager,
                PathGuard([tmp_path]),
                allowed_hosts=frozenset({HOST}),
                public_url=f"https://{HOST}",
            )
    finally:
        manager.shutdown()


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows always uses handle-relative opens"
)
def test_1173_review_unsafe_platform_fails_closed_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "private"
    file.write_bytes(b"private bytes")
    monkeypatch.setattr(os, "supports_dir_fd", set())
    with remote_server(tmp_path) as server:
        status, _, body = request(
            server, "/v1/viewer-file?" + urlencode({"path": str(file)})
        )
        assert status == 503
        assert json.loads(body)["error"]["code"] == "viewer_unavailable"
        assert b"private bytes" not in body


@requires_safe_reads
def test_1173_review_directory_and_fdopen_failures_do_not_leak_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "regular"
    file.write_bytes(b"reads")
    # A subdirectory, not the root: the walk opens its ancestors before refusing.
    directory = tmp_path / "directory"
    directory.mkdir()
    process = psutil.Process()
    open_handles = process.num_handles if sys.platform == "win32" else process.num_fds
    with remote_server(tmp_path) as server:
        request(server, "/v1/health")
        baseline = open_handles()
        for _ in range(8):
            assert (
                request(
                    server, "/v1/viewer-file?" + urlencode({"path": str(directory)})
                )[0]
                == 403
            )
        assert open_handles() <= baseline + 2

        def fdopen_failure(*args: object, **kwargs: object) -> None:
            raise OSError(errno.EIO, "cannot wrap descriptor")

        monkeypatch.setattr(os, "fdopen", fdopen_failure)
        for _ in range(8):
            assert (
                request(server, "/v1/viewer-file?" + urlencode({"path": str(file)}))[0]
                == 403
            )
        assert open_handles() <= baseline + 2


@requires_safe_reads
def test_1173_review_read_error_aborts_without_appending_http_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "reads"
    file.write_bytes(b"0123456789")
    fdopen = os.fdopen

    class BrokenRead:
        def __init__(self, descriptor: int, mode: str) -> None:
            self.file = cast(BinaryIO, fdopen(descriptor, mode))
            self.read_once = False

        def fileno(self) -> int:
            return self.file.fileno()

        def seek(self, offset: int) -> int:
            return self.file.seek(offset)

        def read(self, size: int) -> bytes:
            if self.read_once:
                raise OSError(errno.EIO, "read failure")
            self.read_once = True
            return self.file.read(min(size, 4))

        def close(self) -> None:
            self.file.close()

        def __enter__(self) -> "BrokenRead":
            return self

        def __exit__(self, *exc: object) -> None:
            self.close()

    monkeypatch.setattr(os, "fdopen", BrokenRead)
    with remote_server(tmp_path) as server:
        with pytest.raises(http.client.IncompleteRead) as failure:
            request(server, "/v1/viewer-file?" + urlencode({"path": str(file)}))
        assert failure.value.partial == b"0123"


def test_1173_review_config_lists_saved_and_default_server_settings(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        state_directory_setting(tmp_path)
        + '[server]\nlisten="0.0.0.0:8080"\npublic_url="https://genomes.example"\n'
        + 'allowed_hosts=["genomes.example"]\nviewer_files=true\n'
    )
    result = subprocess.run(
        [*get_cli_command(), "--config", str(config), "config"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    rows = {
        line.split()[0]: line.split()
        for line in result.stdout.splitlines()
        if line.startswith("server.")
    }
    assert rows["server.listen"][1:] == ["0.0.0.0:8080", "[SET]"]
    assert rows["server.public_url"][1:] == ["https://genomes.example", "[SET]"]
    assert rows["server.viewer_files"][-1] == "[SET]"
    assert rows["server.max_connections"][-1] == "[DEFAULT/UNSET]"


def test_1173_review_explicit_cli_port_overrides_saved_listen_port(
    tmp_path: Path,
) -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        saved_port = held.getsockname()[1]
        config = tmp_path / "config.toml"
        config.write_text(
            state_directory_setting(tmp_path)
            + f'[server]\nlisten="127.0.0.1:{saved_port}"\n'
            + 'public_url="https://genomes.example"\nallowed_hosts=["genomes.example"]\n'
        )
        with (tmp_path / "server.log").open("w") as log:
            process = subprocess.Popen(
                [
                    *get_cli_command(),
                    "--config",
                    str(config),
                    "serve",
                    "--listen",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--ready-json",
                ],
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
            )
            assert process.stdout is not None
            stdout = process.stdout
            lines: queue.Queue[str] = queue.Queue()
            reader = threading.Thread(
                target=lambda: lines.put(stdout.readline()), daemon=True
            )
            reader.start()
            try:
                address = json.loads(lines.get(timeout=30))
                port = urlsplit(address["url"]).port
                assert port is not None and port != saved_port
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.request("GET", "/v1/health")
                    response = connection.getresponse()
                    assert response.status == 200
                    assert json.loads(response.read())["ok"]
                finally:
                    connection.close()
            finally:
                process.terminate()
                process.wait(timeout=30)
                reader.join(3)


def test_1173_review_advertised_url_connects_through_allowlisted_proxy(
    tmp_path: Path,
) -> None:
    manager = JobManager(tmp_path / "jobs", config_path=get_config_path())

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            connection = http.client.HTTPConnection(
                "127.0.0.1", backend.server_port, timeout=5
            )
            try:
                connection.request("GET", self.path, headers=dict(self.headers))
                result = connection.getresponse()
                data = result.read()
                self.send_response(result.status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            finally:
                connection.close()

    proxy = LimitedHTTPServer(
        ("127.0.0.1", 0),
        Proxy,
        max_connections=2,
        header_timeout_seconds=10,
    )
    authority = f"127.0.0.1:{proxy.server_port}"
    backend = Server(
        0,
        "private-test-token",
        manager,
        PathGuard([tmp_path]),
        listen="0.0.0.0",
        allowed_hosts=frozenset({authority}),
        public_url=f"http://{authority}",
    )
    threads = [
        threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
        for server in (proxy, backend)
    ]
    for thread in threads:
        thread.start()
    try:
        assert "0.0.0.0" in backend.bind_url and "0.0.0.0" not in backend.url
        destination = urlsplit(backend.url)
        assert destination.hostname is not None
        connection = http.client.HTTPConnection(
            destination.hostname, destination.port, timeout=5
        )
        try:
            connection.request(
                "GET",
                "/v1/health",
                headers={"Authorization": f"Bearer {backend.token}"},
            )
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["ok"]
        finally:
            connection.close()
        assert (
            request(backend, "/v1/health", host=f"0.0.0.0:{backend.server_port}")[0]
            == 403
        )
    finally:
        for server in (proxy, backend):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(3)
        manager.shutdown()
