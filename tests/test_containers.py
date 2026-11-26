"""The container runner against fake Docker executables."""

import os
import subprocess
import sys
import threading
import time

import pytest

from wgsextract_cli.api import CancelToken
from wgsextract_cli.core import containers
from wgsextract_cli.core.execution_context import bound
from wgsextract_cli.core.network_policy import network_options
from wgsextract_cli.core.process_registry import proc_registry
from wgsextract_cli.core.utils import WGSExtractError

from .fake_tools import FakeDocker, require_bare_name_fakes

# `run` blocks like a container whose worker pool waits on a killed worker;
# `events` reports an OOM kill once it is running; `kill` stops it.
FAKE_DOCKER = """#!/bin/sh
state="$(dirname "$0")"
case "$1" in
  run)
    while [ "$1" != "--name" ]; do shift; done
    if [ -e "$state/stop-before-start" ]; then
      printf '%s\\n' "$2" > "$state/created"
    else
      printf '%s\\n' "$2" > "$state/running"
    fi
    printf '%s\\n' $$ > "$state/run.pid"
    exec sleep 60 ;;
  events)
    [ -e "$state/no-oom" ] && exec sleep 60
    if [ -e "$state/oom-on-shutdown" ]; then
      trap 'echo oom; exit' TERM
      touch "$state/events-ready"
      while :; do sleep 0.05; done
    fi
    while [ ! -e "$state/run.pid" ]; do sleep 0.05; done
    echo oom
    exec sleep 60 ;;
  kill)
    sleep 0.1
    [ -e "$state/running" ] || exit 1
    [ "$2" = "$(cat "$state/running")" ] || exit 1
    mv "$state/running" "$state/killed"
    [ -e "$state/no-oom" ] || [ -e "$state/oom-on-shutdown" ] ||
      kill "$(cat "$state/run.pid")" ;;
  rm)
    [ "$2" = "-f" ] && [ "$3" = "$(cat "$state/created")" ] || exit 1
    if [ -e "$state/refuse-removal" ]; then
      echo "daemon refused removal" >&2
      exit 1
    fi
    mv "$state/created" "$state/removed" ;;
esac
"""


@pytest.fixture
def docker(tmp_path, monkeypatch):
    path = tmp_path / "docker"
    path.write_text(FAKE_DOCKER)
    path.chmod(0o755)
    monkeypatch.setattr(containers, "get_tool_path", lambda _tool: str(path))
    return tmp_path


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fake")
def test_oom_kill_stops_a_hung_container_with_the_given_error(docker):
    started = time.monotonic()
    with pytest.raises(WGSExtractError, match="^out of memory$"):
        containers.run_container(
            "image", ["tool"], [str(docker)], oom_error="out of memory"
        )
    assert time.monotonic() - started < 30
    assert (docker / "killed").read_text().startswith("wgsextract-")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fake")
def test_other_failures_keep_their_error(docker, monkeypatch):
    monkeypatch.setattr(containers, "OOM_EVENT_GRACE", 0.1)
    (docker / "no-oom").touch()

    def stop_run():
        while not (docker / "run.pid").exists():
            time.sleep(0.05)
        os.kill(int((docker / "run.pid").read_text()), 15)

    threading.Thread(target=stop_run, daemon=True).start()
    with pytest.raises(subprocess.CalledProcessError) as error:
        containers.run_container("image", ["tool"], [str(docker)], oom_error="oom")
    assert "oom" not in str(error.value)
    assert not (docker / "killed").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fake")
@pytest.mark.parametrize(
    ("oom_error", "before_start", "refuse_removal"),
    [
        (None, False, False),
        ("out of memory", False, False),
        (None, True, False),
        (None, True, True),
    ],
)
def test_issue_1207_cancel_token_cleans_up_or_warns(
    docker, oom_error, before_start, refuse_removal, caplog
):
    (docker / "no-oom").touch()
    if before_start:
        (docker / "stop-before-start").touch()
    if refuse_removal:
        (docker / "refuse-removal").touch()
    token = CancelToken()
    scope = object()
    token.on_cancel(lambda: proc_registry.cancel(scope))

    def cancel_running_container():
        wait_for_file(docker / "run.pid")
        token.cancel()

    worker = threading.Thread(target=cancel_running_container, daemon=True)
    worker.start()
    try:
        with (
            bound(cancel=token, process_scope=scope),
            pytest.raises(subprocess.CalledProcessError),
        ):
            containers.run_container("image", ["tool"], [], oom_error=oom_error)
    finally:
        worker.join()
        proc_registry.accept_new(scope)
    if refuse_removal:
        name = (docker / "created").read_text().strip()
        assert name in caplog.text
        assert "daemon refused removal" in caplog.text
        return
    assert not (docker / "running").exists()
    assert not (docker / "created").exists()
    assert (
        (docker / ("removed" if before_start else "killed"))
        .read_text()
        .startswith("wgsextract-")
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fake")
def test_issue_1207_oom_stops_container_with_refusing_registry(docker):
    (docker / "oom-on-shutdown").touch()
    scope = object()

    def stop_registered_processes():
        wait_for_file(docker / "events-ready")
        wait_for_file(docker / "run.pid")
        proc_registry.cancel(scope)

    worker = threading.Thread(target=stop_registered_processes, daemon=True)
    worker.start()
    try:
        with bound(process_scope=scope), pytest.raises(WGSExtractError, match="memory"):
            containers.run_container("image", ["tool"], [], oom_error="out of memory")
    finally:
        worker.join()
        proc_registry.accept_new(scope)
    assert not (docker / "running").exists()
    assert (docker / "killed").read_text().startswith("wgsextract-")


def wait_for_file(path):
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), path


def test_issue_1207_mount_keeps_colons_and_csv_characters(tmp_path, monkeypatch):
    require_bare_name_fakes()
    engine = FakeDocker(tmp_path / "bin", tmp_path / "dv")
    engine.add_image("image")
    monkeypatch.setattr(
        containers, "get_tool_path", lambda _tool: str(tmp_path / "bin" / "docker")
    )
    folder = tmp_path / 'sample: run, "a"'
    folder.mkdir()
    sample = folder / "sample.txt"
    sample.write_text("mounted")
    result = containers.run_container(
        "image", ["/bin/cat", str(sample)], [str(sample)], capture_output=True
    )
    assert result.stdout == "mounted"


@pytest.mark.parametrize(
    ("mode", "allow_large"), [("full", False), ("low", True), ("off", False)]
)
def test_1591_local_compute_uses_no_network_or_automatic_pull(
    tmp_path, monkeypatch, mode, allow_large
):
    require_bare_name_fakes()
    engine = FakeDocker(tmp_path / "bin", tmp_path / "dv")
    engine.add_image("local-image")
    monkeypatch.setattr(
        containers, "get_tool_path", lambda _tool: str(tmp_path / "bin" / "docker")
    )
    with network_options(mode, allow_large):
        containers.run_container("local-image", ["/usr/bin/true"], [])

    (run,) = engine.runs()
    # contract: isolate local-compute traffic and never pull as part of a launch.
    assert (run["network"], run["pull"]) == ("--network=none", "--pull=never")
