"""Focused server lifecycle regressions."""

import contextlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psutil
import pytest

from tests.home_isolation import state_directory_setting

# The loadgroup scheduler recognizes this import for shared fake_30x fixtures.
from tests.smoke_utils import ensure_fake_data as ensure_fake_data
from tests.smoke_utils import get_cli_command
from tests.test_server import (
    FAKE_DIR,
    Client,
    _job_logs,
    _serve,
    _wait_for,
    interrupt_serve_announcement,
)
from tests.test_server import (
    client as client,
)
from tests.test_server import (
    fake_data as fake_data,
)
from tests.test_server import (
    slow_coverage as slow_coverage,
)
from tests.test_server import (
    variant_tables as variant_tables,
)
from tests.test_server import (
    work as work,
)
from wgsextract_cli.core.last_run import state_directory
from wgsextract_cli.server.jobs import write_private_json


def test_cancel_stops_running_jobs_and_drops_queued_ones(
    tmp_path, client, work, fake_data, slow_coverage
):
    bam, ref = fake_data
    params = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
    _, running = client.request("POST", "/ops/info/calculate-coverage", params)
    _, queued = client.request("POST", "/ops/vcf/snp", {**params, "region": "chrM"})
    assert queued["status"] == "queued"

    status, cancelled = client.request("DELETE", f"/jobs/{queued['id']}")
    assert (status, cancelled["status"], cancelled["exit_code"]) == (
        200,
        "cancelled",
        130,
    )

    _wait_for(lambda: any(slow_coverage.iterdir()), tmp_path)
    tool_pid = int(next(slow_coverage.iterdir()).name)
    status, job = client.request("DELETE", f"/jobs/{running['id']}")
    assert status == 202 and job["cancel_requested"]
    _, job = client.request("GET", f"/jobs/{running['id']}?wait=60")
    assert (job["status"], job["exit_code"]) == ("cancelled", 130)
    assert job["result"]["status"] == "cancelled"
    assert not psutil.pid_exists(tool_pid) or (
        psutil.Process(tool_pid).status() == psutil.STATUS_ZOMBIE
    )
    status, body = client.request("DELETE", f"/jobs/{running['id']}")
    assert (status, body["error"]["code"]) == (409, "finished")
    assert not (work / "snps.vcf.gz").exists()


def test_jobs_outlive_the_server_and_can_be_rerun(
    tmp_path, work, fake_data, slow_coverage
):
    bam, ref = fake_data
    params = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
    with _serve(tmp_path, [work, FAKE_DIR]) as client:
        _, done = client.request(
            "POST", "/ops/info?wait=100", {"input": str(bam), "outdir": str(work)}
        )
        assert done["status"] == "succeeded", _job_logs(tmp_path)
        _, running = client.request("POST", "/ops/info/calculate-coverage", params)
        _wait_for(lambda: any(slow_coverage.iterdir()), tmp_path)
    # The server stopped, as on Ctrl-C, with the coverage job still running.

    with _serve(tmp_path, [work, FAKE_DIR]) as client:
        _, listed = client.request("GET", "/jobs")
        statuses = {job["id"]: job["status"] for job in listed["jobs"]}
        assert statuses == {done["id"]: "succeeded", running["id"]: "interrupted"}
        _, job = client.request("GET", f"/jobs/{done['id']}")
        assert job["result"]["metrics"]["sorted"] is True

        status, rerun = client.request("POST", f"/jobs/{running['id']}/rerun")
        assert status == 202 and rerun["id"] != running["id"]
        _, rerun = client.request("GET", f"/jobs/{rerun['id']}?wait=1")
        assert rerun["status"] == "running"


def test_serve_command_publishes_its_address_and_cleans_up(tmp_path, work):
    proc = subprocess.Popen(
        [*get_cli_command(), "serve", "--allow", str(work), "--ready-json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    discovery = tmp_path / "home" / "missing"
    try:
        assert proc.stderr is not None
        banner = ""
        while "Stop with Ctrl-C" not in banner:
            line = proc.stderr.readline()
            assert line, f"serve exited: {banner}"
            banner += line
        discovery = Path(banner.split("(also in ")[1].split(",")[0])
        info = json.loads(discovery.read_text())
        assert info["url"] in banner and info["token"] in banner
        # --ready-json: the program that started the server reads it from stdout.
        assert proc.stdout is not None
        assert json.loads(proc.stdout.readline()) == info
        assert info["client_job_ids"] is True
        if sys.platform != "win32":
            assert discovery.stat().st_mode & 0o777 == 0o600
        client = Client(info["url"].removesuffix("/v1"))
        status, index = client.request("GET", "", token=info["token"])
        assert status == 200 and str(work.resolve()) in index["allowed_paths"]
    finally:
        proc.send_signal(signal.SIGINT if sys.platform != "win32" else signal.SIGTERM)
        proc.wait(30)
    if sys.platform != "win32":
        assert proc.returncode == 0
        assert not discovery.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
@pytest.mark.parametrize(
    ("stop_signal", "remote"),
    [(signal.SIGINT, False), (signal.SIGTERM, True)],
    ids=["loopback-sigint", "wildcard-sigterm"],
)
def test_1254_signal_during_http_request_dispatch_exits_cleanly(
    tmp_path: Path, work: Path, stop_signal: int, remote: bool
) -> None:
    # Let the actual HTTP request finish before returning from the OS thread
    # startup boundary, then deliver the stop signal before the accept loop resumes.
    code = f"""
import os, socket, sys, threading
from wgsextract_cli.main import main

request_port = int(sys.argv.pop(1))
peer_port = None
accept = socket.socket.accept
def accept_peer(listener):
    global peer_port
    connection, address = accept(listener)
    if threading.current_thread() is threading.main_thread():
        peer_port = address[1]
    return connection, address

start = threading.Thread.start
def interrupt_start(thread):
    start(thread)
    if threading.current_thread() is threading.main_thread() and peer_port == request_port:
        thread.join(timeout=10)
        assert not thread.is_alive()
        os.kill(os.getpid(), {stop_signal})

socket.socket.accept = accept_peer
threading.Thread.start = interrupt_start
main()
"""
    config = tmp_path / "config.toml"
    config.write_text(
        state_directory_setting(tmp_path)
        + '[server]\nallowed_hosts=["genomes.example"]\n'
    )
    args = ["--config", str(config), "serve", "--allow", str(work), "--ready-json"]
    if remote:
        args += ["--listen", "0.0.0.0", "--public-url", "https://genomes.example"]
    # Reserve the authenticated client's source port so the hook can ignore
    # unrelated connections, even if a probe beats the test to the listener.
    with socket.socket() as peer:
        peer.bind(("127.0.0.1", 0))
        with subprocess.Popen(
            [sys.executable, "-c", code, str(peer.getsockname()[1]), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ) as proc:
            try:
                assert proc.stdout is not None
                info = json.loads(proc.stdout.readline())
                bind = urlsplit(info.get("bind_url", info["url"]))
                assert bind.port is not None
                url = f"http://127.0.0.1:{bind.port}"
                headers = {"Host": "genomes.example"} if remote else {}
                status, _ = Client(url).request("GET", "", token=None, headers=headers)
                assert status == 401

                peer.settimeout(10)
                peer.connect(("127.0.0.1", bind.port))
                with contextlib.closing(
                    http.client.HTTPConnection("127.0.0.1", bind.port)
                ) as conn:
                    conn.sock = peer
                    conn.request(
                        "GET",
                        "/v1",
                        headers={
                            **headers,
                            "Authorization": f"Bearer {info['token']}",
                            "Connection": "close",
                        },
                    )
                    with conn.getresponse() as response:
                        assert response.status == 200
                        assert (
                            str(work.resolve()) in json.load(response)["allowed_paths"]
                        )
                assert proc.wait(timeout=10) == 0
                assert not (tmp_path / "state" / "server" / "server.json").exists()
            finally:
                if proc.poll() is None:
                    proc.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_799_interrupting_http_discovery_announcement_exits_cleanly(
    work: Path, stop_signal: int
) -> None:
    proc = interrupt_serve_announcement(work, stop_signal)
    assert json.loads(proc.stdout)["url"].startswith("http://127.0.0.1:")
    assert proc.returncode == 0
    assert not (state_directory() / "server" / "server.json").exists()


@pytest.mark.parametrize("existing", [False, True])
def test_799_interrupted_private_json_never_publishes_partial_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    path = tmp_path / "server.json"
    previous = {"pid": 123, "token": "previous"}
    if existing:
        write_private_json(path, previous)
    visible: list[str | None] = []
    modes: list[int] = []
    fdopen = os.fdopen

    @contextlib.contextmanager
    def interrupted_fdopen(fd: int, *args: Any, **kwargs: Any) -> Iterator[Any]:
        with fdopen(fd, *args, **kwargs) as stream:

            class InterruptedWriter:
                def write(self, text: str) -> int:
                    stream.write(text)
                    stream.flush()
                    visible.append(path.read_text() if path.exists() else None)
                    modes.append(os.fstat(stream.fileno()).st_mode & 0o777)
                    raise KeyboardInterrupt

            yield InterruptedWriter()

    # OS file-stream boundary: interrupt after the first real JSON write.
    monkeypatch.setattr(os, "fdopen", interrupted_fdopen)
    with pytest.raises(KeyboardInterrupt):
        write_private_json(path, {"pid": 456, "token": "replacement"})
    assert len(visible) == 1
    if existing:
        assert json.loads(visible[0] or "") == previous
        assert json.loads(path.read_text()) == previous
    else:
        assert visible == [None]
        assert not path.exists()
    if sys.platform != "win32":
        assert modes == [0o600]
    assert set(tmp_path.iterdir()) == ({path} if existing else set())


def test_serve_exits_with_its_parent_and_frees_the_state_folder(work):
    # #523 review: a server orphaned by a hard-killed GUI kept the jobs folder's
    # lock, so every later server refused to start.
    def serve() -> subprocess.Popen[str]:
        return subprocess.Popen(
            [*get_cli_command(), "--parent-pid", str(parent.pid), "serve"]
            + ["--allow", str(work), "--ready-json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def ready_line(proc: subprocess.Popen[str]) -> dict[str, Any]:
        assert proc.stdout is not None
        return json.loads(proc.stdout.readline())

    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    first = serve()
    try:
        assert "url" in ready_line(first)
        second = serve()
        refused = ready_line(second)
        assert "Another wgsextract server" in refused["error"]["message"]
        assert second.wait(60) != 0

        parent.kill()
        parent.wait(10)
        first.wait(30)
        parent = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"]
        )
        third = serve()
        try:
            assert "url" in ready_line(third)
        finally:
            third.kill()
            third.wait(30)
    finally:
        for proc in (first, parent):
            proc.kill()
            proc.wait(30)
