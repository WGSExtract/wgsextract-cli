"""#1160: explicit network exposure and guarded, authenticated viewer reads."""

import contextlib
import http.client
import json
import os
import queue
import secrets
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlencode

import pytest

from tests.smoke_utils import get_cli_command, state_directory_setting
from wgsextract_cli.core.config import (
    get_config_path,
    save_config,
    scoped_config,
    typed_settings,
)
from wgsextract_cli.server.http_api import Server
from wgsextract_cli.server.jobs import JobManager
from wgsextract_cli.server.operations import PathGuard

ORIGIN = "https://app.example"
HOST = "genomes.example"


@contextlib.contextmanager
def remote_server(
    tmp_path: Path, *, enabled: bool = True, listen: str = "0.0.0.0"
) -> Iterator[Server]:
    manager = JobManager(tmp_path / secrets.token_hex(8), config_path=get_config_path())
    server = Server(
        0,
        secrets.token_urlsafe(32),
        manager,
        PathGuard([tmp_path]),
        listen=listen,
        allowed_hosts=frozenset({HOST}),
        allowed_origins=frozenset({ORIGIN}),
        viewer_files=enabled,
    )
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        manager.shutdown()


def request(
    server: Server,
    path: str,
    *,
    method: str = "GET",
    token: bool = True,
    origin: str | None = ORIGIN,
    host: str = HOST,
    range_header: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    headers = {"Host": host}
    if token:
        headers["Authorization"] = f"Bearer {server.token}"
    if origin is not None:
        headers["Origin"] = origin
    if range_header:
        headers["Range"] = range_header
    try:
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_issue_1160_network_listener_refuses_empty_token_before_binding(
    tmp_path: Path,
) -> None:
    manager = JobManager(tmp_path / "jobs", config_path=get_config_path())
    try:
        with pytest.raises(ValueError, match="token"):
            Server(
                0,
                "",
                manager,
                PathGuard([tmp_path]),
                listen="0.0.0.0",
                allowed_hosts=frozenset({HOST}),
            )
        with pytest.raises(ValueError, match="token"):
            Server(0, "", manager, PathGuard([tmp_path]), viewer_files=True)
        with pytest.raises(ValueError, match="allowed_hosts"):
            Server(0, "token", manager, PathGuard([tmp_path]), listen="0.0.0.0")
        with pytest.raises(ValueError, match="wildcards"):
            Server(
                0,
                "token",
                manager,
                PathGuard([tmp_path]),
                listen="0.0.0.0",
                allowed_hosts=frozenset({"*"}),
            )
    finally:
        manager.shutdown()


def test_issue_1160_external_hosts_origins_and_health_need_auth(tmp_path: Path) -> None:
    with remote_server(tmp_path) as server:
        for kwargs, status, code in (
            ({"token": False}, 401, "unauthorized"),
            ({"origin": "https://evil.example"}, 403, "bad_origin"),
            ({"host": "evil.example"}, 403, "bad_host"),
        ):
            actual, _, body = request(server, "/v1/health", **kwargs)
            assert (actual, json.loads(body)["error"]["code"]) == (status, code)
        assert request(server, "/v1/health")[0] == 200


def test_issue_1160_viewer_is_disabled_by_default(tmp_path: Path) -> None:
    with remote_server(tmp_path, enabled=False) as server:
        assert request(server, "/v1/viewer-file?path=" + str(tmp_path))[0] == 404


@pytest.mark.parametrize(
    "range_header,expected_status,expected",
    [
        (None, 200, b"0123456789"),
        ("bytes=2-5", 206, b"2345"),
        ("bytes=6-", 206, b"6789"),
        ("bytes=-3", 206, b"789"),
        ("bytes=8-999", 206, b"89"),
        ("bytes=10-11", 416, b""),
        ("bytes=5-2", 416, b""),
        ("bytes=0-1,3-4", 416, b""),
    ],
)
def test_issue_1160_viewer_ranges_match_file_bytes(
    tmp_path: Path, range_header: str | None, expected_status: int, expected: bytes
) -> None:
    file = tmp_path / secrets.token_hex(12)
    file.write_bytes(b"0123456789")
    with remote_server(tmp_path) as server:
        target = "/v1/viewer-file?" + urlencode({"path": str(file)})
        status, headers, body = request(server, target, range_header=range_header)
        assert (status, body) == (expected_status, expected)
        assert headers["Access-Control-Allow-Origin"] == ORIGIN
        assert "Access-Control-Allow-Credentials" not in headers
        if status == 206:
            start = b"0123456789".index(expected)
            assert (
                headers["Content-Range"]
                == f"bytes {start}-{start + len(expected) - 1}/10"
            )
        assert (
            request(server, target, method="HEAD", range_header=range_header)[2] == b""
        )
        assert request(server, target, token=False)[0] == 401


def test_issue_1160_viewer_rejects_traversal_symlink_escape_and_nonregular(
    tmp_path: Path,
) -> None:
    root = tmp_path / secrets.token_hex(12)
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    link = root / "escape"
    link.symlink_to(outside)
    with remote_server(root) as server:
        for file in (root / ".." / "outside", link, root):
            status, _, body = request(
                server, "/v1/viewer-file?" + urlencode({"path": str(file)})
            )
            assert status == 403
            assert b"private" not in body
        if hasattr(os, "mkfifo"):
            fifo = root / "fifo"
            os.mkfifo(fifo)
            assert (
                request(server, "/v1/viewer-file?" + urlencode({"path": str(fifo)}))[0]
                == 403
            )


def test_issue_1160_explicit_large_range_streams_and_fallback_is_bounded(
    tmp_path: Path,
) -> None:
    file = tmp_path / secrets.token_hex(12)
    size = 33 * 1024 * 1024
    with file.open("wb") as output:
        output.truncate(size)
    with remote_server(tmp_path) as server:
        target = "/v1/viewer-file?" + urlencode({"path": str(file)})
        assert request(server, target)[0] == 416
        status, headers, body = request(
            server, target, range_header=f"bytes=0-{size - 1}"
        )
        assert status == 206
        assert len(body) == size
        assert body.count(0) == size
        assert headers["Content-Length"] == str(size)


def test_issue_1160_server_table_config_round_trip(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[server]\nlisten="127.0.0.2:1234"\nallowed_hosts=["genomes.example"]\nallowed_origins=["https://app.example"]\nviewer_files=true\n'
    )
    with scoped_config(config):
        options = typed_settings().server
        assert options.listen == "127.0.0.2:1234"
        assert options.viewer_files
        save_config({"server": {"viewer_files": False}})
        assert not typed_settings().server.viewer_files
        assert typed_settings().server.listen == "127.0.0.2:1234"
        save_config({"server": {"listen": None}})
        assert typed_settings().server.listen == "127.0.0.1"
        save_config({"server": None})
        assert "server" not in config.read_text()


def test_issue_1160_allowed_viewer_origin_can_read_errors(tmp_path: Path) -> None:
    missing = tmp_path / secrets.token_hex(12)
    with remote_server(tmp_path) as server:
        target = "/v1/viewer-file?" + urlencode({"path": str(missing)})
        status, headers, body = request(server, target)
        assert status == 404
        assert json.loads(body)["error"]["code"] == "not_found"
        assert headers["Access-Control-Allow-Origin"] == ORIGIN
        status, headers, body = request(server, target, token=False)
        assert status == 401
        assert json.loads(body)["error"]["code"] == "unauthorized"
        assert headers["Access-Control-Allow-Origin"] == ORIGIN
        status, headers, _ = request(server, target, origin="https://evil.example")
        assert status == 403
        assert "Access-Control-Allow-Origin" not in headers


def test_issue_1160_preflight_is_exact_origin_without_general_token(
    tmp_path: Path,
) -> None:
    with remote_server(tmp_path) as server:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        try:
            headers = {
                "Host": HOST,
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization, range",
            }
            connection.request("OPTIONS", "/v1/viewer-file", headers=headers)
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader("Access-Control-Allow-Origin") == ORIGIN
            assert response.getheader("Access-Control-Allow-Credentials") is None
            response.read()
            headers["Origin"] = "https://evil.example"
            connection.request("OPTIONS", "/v1/viewer-file", headers=headers)
            response = connection.getresponse()
            assert response.status == 403
            assert response.getheader("Access-Control-Allow-Origin") is None
            response.read()
        finally:
            connection.close()


@pytest.mark.parametrize("listen", ["127.0.0.2", "0.0.0.0"])
def test_issue_1160_cli_listen_override_binds_chosen_interface_and_warns(
    tmp_path: Path, listen: str
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        state_directory_setting(tmp_path)
        + '[server]\nlisten="127.0.0.1"\nallowed_hosts=["genomes.example"]\n'
    )
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [
                *get_cli_command(),
                "--config",
                str(config),
                "serve",
                "--listen",
                listen,
                "--allow",
                str(tmp_path),
                "--ready-json",
            ],
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        lines: queue.Queue[str] = queue.Queue()
        assert process.stdout is not None
        stdout = process.stdout
        reader = threading.Thread(
            target=lambda: lines.put(stdout.readline()), daemon=True
        )
        reader.start()
        try:
            address = json.loads(lines.get(timeout=30))
            assert address["url"].startswith(f"http://{listen}:")
            port = int(address["url"].split(":")[-1].split("/")[0])
            connection = http.client.HTTPConnection(
                "127.0.0.2" if listen == "127.0.0.2" else "127.0.0.1", port, timeout=5
            )
            try:
                connection.request(
                    "GET",
                    "/v1/health",
                    headers={
                        "Host": f"{listen}:{port}" if listen == "127.0.0.2" else HOST,
                        "Authorization": f"Bearer {address['token']}",
                    },
                )
                response = connection.getresponse()
                assert response.status == 200
                assert json.loads(response.read())["ok"]
            finally:
                connection.close()
        finally:
            process.terminate()
            process.wait(timeout=30)
            reader.join(timeout=5)
        log.seek(0)
        banner = log.read()
        if listen == "0.0.0.0":
            assert (
                "0.0.0.0" in banner and "entire authenticated HTTP/JSON API" in banner
            )
            assert "unencrypted" in banner
