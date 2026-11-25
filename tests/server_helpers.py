"""Loopback HTTP fixtures and MCP clients for server integration tests."""

import contextlib
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from queue import Empty, Queue
from typing import Any

import pytest

from tests.smoke_utils import get_cli_command
from wgsextract_cli.core.config import get_config_path
from wgsextract_cli.server.http_api import Server
from wgsextract_cli.server.jobs import JobManager
from wgsextract_cli.server.operations import PathGuard

FAKE_DIR = Path(__file__).resolve().parents[1] / "out" / "fake_30x"
TOKEN = "test-token"


class Client:
    def __init__(self, url: str) -> None:
        self.url = url + "/v1"

    def request(
        self,
        method: str,
        path: str,
        body: object = None,
        *,
        token: str | None = TOKEN,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, _decode(response)
        except urllib.error.HTTPError as e:
            return e.code, _decode(e)

    def events(self, job_id: str) -> list[dict[str, Any]]:
        """A job's server-sent events, read until the stream ends."""
        request = urllib.request.Request(f"{self.url}/jobs/{job_id}/events")
        request.add_header("Authorization", f"Bearer {TOKEN}")
        with urllib.request.urlopen(request, timeout=300) as response:
            assert response.headers["Content-Type"] == "text/event-stream"
            text = response.read().decode()
        return [
            json.loads(line.removeprefix("data: "))
            for line in text.splitlines()
            if line.startswith("data: ")
        ]


def _decode(response: Any) -> Any:
    text = response.read().decode()
    if response.headers.get("Content-Type") == "application/json":
        return json.loads(text)
    return text


@pytest.fixture
def work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work


@contextlib.contextmanager
def _serve(
    tmp_path: Path,
    roots: list[Path],
    max_jobs: int = 1,
    *,
    allow_destructive: bool = False,
    card: bool | None = None,
) -> Iterator[Client]:
    guard = PathGuard(roots, allow_destructive=allow_destructive)
    manager = JobManager(
        tmp_path / "state" / "jobs",
        config_path=get_config_path(),
        max_jobs=max_jobs,
        allowed=roots,
        card=card,
    )
    server = Server(0, TOKEN, manager, guard)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(server.url)
    finally:
        server.shutdown()
        server.server_close()
        manager.shutdown()


@pytest.fixture
def client(tmp_path: Path, work: Path) -> Iterator[Client]:
    with _serve(tmp_path, [work, FAKE_DIR]) as client:
        yield client


def interrupt_serve_announcement(
    work: Path, stop_signal: int, *args: str
) -> subprocess.CompletedProcess[str]:
    # The OS stream boundary: interrupt at the ready line, before HTTP startup,
    # rather than depending on subprocess scheduling.
    code = f"""
import os, signal, sys
from wgsextract_cli.main import main
class InterruptOnReady:
    def write(self, text):
        result = sys.__stdout__.write(text)
        if '"url"' in text:
            sys.__stdout__.flush()
            os.kill(os.getpid(), {stop_signal})
        return result
    def flush(self):
        sys.__stdout__.flush()
sys.stdout = InterruptOnReady()
main()
"""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            "serve",
            "--ready-json",
            "--allow",
            str(work),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


class Mcp:
    """A `wgsextract serve --mcp` subprocess, spoken to line by line."""

    def __init__(self, *args: str) -> None:
        self.proc = subprocess.Popen(
            [*get_cli_command(), "serve", "--mcp", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.messages: Queue[dict[str, Any]] = Queue()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()
        reply = self.request(
            "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}
        )
        assert reply["result"]["protocolVersion"] == "2025-06-18"
        self.send({"method": "notifications/initialized"})

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.messages.put(json.loads(line))

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: Any) -> dict[str, Any]:
        self.next_id += 1
        self.send({"id": self.next_id, "method": method, "params": params})
        while True:
            try:
                message = self.messages.get(timeout=300)
            except Empty:
                raise AssertionError(f"no reply to {method}") from None
            if message.get("id") == self.next_id:
                return message

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        return reply.get("result") or reply

    def close(self) -> int:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        return self.proc.wait(60)
