"""Remote certification stays bound to the pushed head without remote credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.ci_certify_remote import REPOSITORY, RESULT, certify_remote
from scripts.ci_certify_ssh import CANCEL, HOST, SUPERVISOR, ssh_command


@pytest.fixture
def remote_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q", "--bare", "origin.git"], check=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "ci/evil;echo-owned", "repo"], check=True
    )
    monkeypatch.chdir(tmp_path / "repo")
    Path(".gitignore").write_text("out/\n")
    subprocess.run(["git", "add", ".gitignore"], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "Remote certification regression",
        ],
        check=True,
    )
    subprocess.run(["git", "remote", "add", "origin", "../origin.git"], check=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD"], check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    return {
        "number": 972,
        "state": "open",
        "base": {"sha": "b" * 40},
        "head": {
            "sha": sha,
            "ref": "ci/evil;echo-owned",
            "repo": {"full_name": REPOSITORY},
        },
    }


def transport(
    monkeypatch: pytest.MonkeyPatch,
    pr: dict[str, Any],
    *,
    state: str = "success",
    code: int = 0,
    binding: str | None = None,
    before_binding: str | None = None,
    layer: str = "macos-arm64",
    disconnect: bool = False,
    moved_pr: bool = False,
    lock_refused: bool = False,
    cleanup_refused: bool = False,
    terminated: bool = False,
) -> tuple[list[dict[str, str]], list[str]]:
    popen = subprocess.Popen
    statuses: list[dict[str, str]] = []
    locks: list[str] = []

    def boundary(command: list[str], *args: Any, **kwargs: Any) -> Any:
        output = ""
        exit_code = 0
        if command[0] == "ssh":
            if command[-1] == ssh_command(CANCEL)[-1]:
                return popen(
                    [
                        sys.executable,
                        "-c",
                        "import sys; sys.stdin.readline(); "
                        f"print('stopped'); sys.exit({int(cleanup_refused)})",
                    ],
                    *args,
                    **kwargs,
                )
            if terminated:
                os.kill(os.getpid(), signal.SIGTERM)
            result = {
                "sha_before": before_binding
                if before_binding is not None
                else pr["head"]["sha"],
                "sha_after": binding if binding is not None else pr["head"]["sha"],
                "base": pr["base"]["sha"],
                "platform": "macos-arm64",
                "layer": layer,
                "state": state,
            }
            output = "live remote test output\n"
            if not disconnect:
                output += RESULT + json.dumps(result) + "\n"
            exit_code = code
        elif command[0] == "gh":
            if "comment" not in command:
                fields = dict(part.split("=", 1) for part in command if "=" in part)
                if "state" in fields:
                    statuses.append(fields)
                else:
                    response = (
                        {**pr, "head": {**pr["head"], "sha": "c" * 40}}
                        if moved_pr
                        else pr
                    )
                    output = json.dumps(response)
        elif command[0] == "agent-coord":
            locks.append(command[1])
            exit_code = 1 if command[1] == "lock" and lock_refused else 0
        else:
            return popen(command, *args, **kwargs)
        # A real pipe/process preserves streaming, EOF and exit-code behavior.
        return popen(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stdin.readline() if {command[0] == 'ssh'!r} else None; "
                f"sys.stdout.write({output!r}); sys.exit({exit_code})",
            ],
            *args,
            **kwargs,
        )

    monkeypatch.setattr(subprocess, "Popen", boundary)
    return statuses, locks


def certify(pr: dict[str, Any]) -> None:
    certify_remote(
        REPOSITORY,
        pr,
        pr["head"]["sha"],
        argparse.Namespace(platform_only=True, generic_only=False, workers=1),
    )


def test_972_remote_success_streams_and_posts_local_log_digest(
    remote_pr: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    statuses, locks = transport(monkeypatch, remote_pr)
    certify(remote_pr)
    sha = remote_pr["head"]["sha"]
    log = Path("out/certify") / sha / f"remote-{HOST}/macos-arm64/run.log"
    digest = hashlib.sha256(log.read_bytes()).hexdigest()
    assert "live remote test output" in capsys.readouterr().out
    assert "live remote test output" in log.read_text()
    assert [status["state"] for status in statuses] == ["pending", "success"]
    assert HOST in statuses[-1]["description"]
    assert digest in statuses[-1]["description"]
    assert locks == ["lock", "unlock"]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"state": "failure", "code": 1}, "failure"),
        ({"state": "error", "code": 2}, "error"),
        ({"binding": "d" * 40}, "error"),
        ({"binding": ""}, "error"),
        ({"before_binding": "d" * 40}, "error"),
        ({"disconnect": True, "code": 255}, "error"),
        ({"moved_pr": True}, "error"),
        ({"state": "success", "code": 1}, "error"),
        ({"lock_refused": True}, "error"),
        ({"cleanup_refused": True}, "error"),
        pytest.param(
            {"terminated": True},
            "error",
            marks=pytest.mark.skipif(
                sys.platform == "win32",
                reason="Windows TerminateProcess cannot deliver a catchable SIGTERM",
            ),
        ),
    ],
)
def test_972_refusals_never_publish_test_failure(
    remote_pr: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, Any],
    expected: str,
) -> None:
    statuses, locks = transport(monkeypatch, remote_pr, **options)
    with pytest.raises((ValueError, subprocess.SubprocessError)):
        certify(remote_pr)
    assert [status["state"] for status in statuses] == ["pending", expected]
    assert locks == (
        ["lock"]
        if options.get("lock_refused") or options.get("cleanup_refused")
        else ["lock", "unlock"]
    )


def test_972_shell_payload_in_sha_is_refused_without_execution(
    remote_pr: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses, locks = transport(monkeypatch, remote_pr)
    remote_pr["base"]["sha"] = "$(touch owned)"
    with pytest.raises(ValueError, match="full commit SHAs"):
        certify(remote_pr)
    assert statuses[-1]["state"] == "error"
    assert not Path("owned").exists()
    assert locks == ["lock", "unlock"]


def test_972_generic_only_posts_only_its_remote_layer(
    remote_pr: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses, locks = transport(monkeypatch, remote_pr, layer="generic")
    certify_remote(
        REPOSITORY,
        remote_pr,
        remote_pr["head"]["sha"],
        argparse.Namespace(platform_only=False, generic_only=True, workers=1),
    )
    assert [status["state"] for status in statuses] == ["pending", "success"]
    assert {status["context"] for status in statuses} == {"local-ci/generic"}
    assert locks == ["lock", "unlock"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SSH process groups")
def test_972_disconnect_reaps_remote_test_children(tmp_path: Path) -> None:
    if shutil.disk_usage(tmp_path).free < 30 * 1024**3:
        pytest.skip("Remote supervisor integration needs its 30 GiB disk reserve")
    child_script = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)']); "
        "print(child.pid, flush=True); time.sleep(300)"
    )
    run_id = "a" * 32
    with subprocess.Popen(
        [sys.executable, "-u", "-c", SUPERVISOR],
        env={**os.environ, "HOME": str(tmp_path)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps({"id": run_id, "script": child_script}) + "\n")
        process.stdin.flush()
        pid = int(process.stdout.readline())
        process.stdin.close()
        assert process.wait(timeout=15) == 2
        output = process.stdout.read()
    assert "disconnected" in output
    control = tmp_path / "src/wgsextract-cli/out/certify/remote-control"
    assert (control / (run_id + ".done")).is_file()
    status = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    assert not status.stdout.strip() or status.stdout.strip().startswith("Z")
