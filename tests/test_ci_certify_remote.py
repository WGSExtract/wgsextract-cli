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

from scripts.ci_certify import host_platform
from scripts.ci_certify_remote import BOOTSTRAP, REPOSITORY, certify_remote
from scripts.ci_certify_remote_worker import execute
from scripts.ci_certify_ssh import CANCEL, PROBE, RESULT, SUPERVISOR, program_line


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
    result_platform: str = "macos-arm64",
    unavailable: str = "",
    disconnect: bool = False,
    moved_pr: bool = False,
    lock_refused: bool = False,
    cleanup_refused: bool = False,
    terminated: bool = False,
    cancel_interrupted: bool = False,
    forwarded_environment: str = "",
    transport_does_not_start: bool = False,
    lock_interrupted: bool = False,
) -> tuple[list[dict[str, str]], list[str]]:
    popen = subprocess.Popen
    statuses: list[dict[str, str]] = []
    locks: list[str] = []

    def boundary(command: list[str], *args: Any, **kwargs: Any) -> Any:
        output = ""
        exit_code = 0
        if command[0] == "ssh":
            if "-G" in command:
                return popen(
                    [sys.executable, "-c", f"print({forwarded_environment!r}, end='')"],
                    *args,
                    **kwargs,
                )
            alias = command[-2]
            if transport_does_not_start and kwargs.get("encoding"):
                raise OSError("SSH did not start")
            if terminated and kwargs.get("encoding"):
                os.kill(os.getpid(), signal.SIGTERM)
            if cancel_interrupted and "stderr" not in kwargs:
                raise KeyboardInterrupt
            probe_platform = "macos-arm64" if alias == "randy-mini" else "windows-x64"
            result = {
                "sha_before": before_binding
                if before_binding is not None
                else pr["head"]["sha"],
                "sha_after": binding if binding is not None else pr["head"]["sha"],
                "base": pr["base"]["sha"],
                "platform": result_platform,
                "layer": layer,
                "state": state,
            }
            output = "live remote test output\n"
            if not disconnect:
                output += RESULT + json.dumps(result) + "\n"
            return popen(
                [
                    sys.executable,
                    "-c",
                    "import sys; first = sys.stdin.readline(); "
                    f"probe = first.rstrip() == {program_line(PROBE).rstrip()!r}; "
                    f"cancel = first.rstrip() == {program_line(CANCEL).rstrip()!r}; "
                    f"ready = {alias != unavailable!r}; "
                    "sys.stdin.readline() if not probe else None; "
                    f"print('WGSE_READY {probe_platform}' if ready else 'offline') if probe "
                    "else print('stopped') if cancel "
                    f"else sys.stdout.write({output!r}); "
                    f"sys.exit(int(not ready) if probe else {int(cleanup_refused)} if cancel else {code})",
                ],
                *args,
                **kwargs,
            )
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
            if command[1] == "lock" and lock_interrupted:
                signal.raise_signal(signal.SIGTERM)
        else:
            return popen(command, *args, **kwargs)
        # A real pipe/process preserves streaming, EOF and exit-code behavior.
        return popen(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stdout.write({output!r}); sys.exit({exit_code})",
            ],
            *args,
            **kwargs,
        )

    monkeypatch.setattr(subprocess, "Popen", boundary)
    return statuses, locks


def certify(
    pr: dict[str, Any],
    *,
    remote: str = "randy-mini",
    generic: bool = False,
    config: Path = Path("config.toml"),
) -> None:
    certify_remote(
        REPOSITORY,
        pr,
        pr["head"]["sha"],
        argparse.Namespace(
            platform_only=not generic,
            generic_only=generic,
            remote=remote,
            remote_config=config,
            prepared_only=False,
            workers=1,
        ),
    )


def test_972_remote_success_streams_and_posts_local_log_digest(
    remote_pr: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    statuses, locks = transport(monkeypatch, remote_pr)
    certify(remote_pr)
    sha = remote_pr["head"]["sha"]
    log = Path("out/certify") / sha / "remote-randy-mini/macos-arm64/run.log"
    digest = hashlib.sha256(log.read_bytes()).hexdigest()
    assert "live remote test output" in capsys.readouterr().out
    assert "live remote test output" in log.read_text()
    assert [status["state"] for status in statuses] == ["pending", "success"]
    assert "randy-mini" in statuses[-1]["description"]
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
        ({"result_platform": "windows-x64"}, "error"),
        ({"lock_refused": True}, "error"),
        ({"cleanup_refused": True}, "error"),
        ({"cancel_interrupted": True}, "error"),
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
    assert [status["state"] for status in statuses] == (
        [] if options.get("lock_refused") else ["pending", expected]
    )
    assert locks == (
        ["lock"]
        if any(
            options.get(flag)
            for flag in ("lock_refused", "cleanup_refused", "cancel_interrupted")
        )
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
    certify(remote_pr, generic=True)
    assert [status["state"] for status in statuses] == ["pending", "success"]
    assert {status["context"] for status in statuses} == {"local-ci/generic"}
    assert locks == ["lock", "unlock"]


def test_1632_prepared_only_worker_refuses_missing_inputs_without_setup(
    remote_pr: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = shutil.disk_usage(Path.cwd())
    monkeypatch.setattr(
        shutil, "disk_usage", lambda _path: usage._replace(free=30 * 1024**3)
    )
    # No Pixi executable can run setup or perform a download.
    monkeypatch.setenv("PATH", str(Path(shutil.which("git") or "").parent))
    head = remote_pr["head"]["sha"]
    with pytest.raises(ValueError, match="Missing CI environment"):
        execute(head, head, "generic", host_platform(), 1, prepared_only=True)
    assert not Path(".pixi").exists()


def test_1495_selects_next_native_windows_host(
    remote_pr: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[[certification.hosts]]\nalias = "minipc"\nplatform = "windows-x64"\n'
        '[[certification.hosts]]\nalias = "seraph"\nplatform = "windows-x64"\n'
    )
    statuses, locks = transport(
        monkeypatch,
        remote_pr,
        unavailable="minipc",
        result_platform="windows-x64",
        layer="windows-x64",
    )
    certify(remote_pr, remote="windows-x64", config=config)
    assert [status["state"] for status in statuses] == ["pending", "success"]
    assert statuses[-1]["context"] == "local-ci/windows-x64"
    assert statuses[-1]["description"].startswith("seraph ")
    assert locks == ["lock", "unlock"]


def test_1495_rejects_unregistered_ssh_options(
    remote_pr: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    statuses, locks = transport(monkeypatch, remote_pr)
    with pytest.raises(ValueError, match="No registered certification host"):
        certify(remote_pr, remote="-oProxyCommand=anything")
    assert not statuses and not locks


@pytest.mark.parametrize(
    "forwarding", ["setenv GITHUB_TOKEN=dummy-value\n", "sendenv unused PATH\n"]
)
def test_1495_refuses_ssh_environment_forwarding_without_publishing(
    remote_pr: dict[str, Any], monkeypatch: pytest.MonkeyPatch, forwarding: str
) -> None:
    statuses, locks = transport(
        monkeypatch, remote_pr, forwarded_environment=forwarding
    )
    with pytest.raises(ValueError, match="forwards environment data"):
        certify(remote_pr)
    assert not statuses and not locks


def test_1495_ssh_startup_failure_releases_unused_host(
    remote_pr: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    statuses, locks = transport(
        monkeypatch, remote_pr, transport_does_not_start=True, cleanup_refused=True
    )
    with pytest.raises(OSError, match="SSH did not start"):
        certify(remote_pr)
    assert statuses[-1]["state"] == "error"
    assert locks == ["lock", "unlock"]


def test_1495_invalid_registration_points_to_config_file(
    remote_pr: dict[str, Any], tmp_path: Path
) -> None:
    config = tmp_path / "host-registry.toml"
    config.write_text("[[certification.hosts]\n")
    with pytest.raises(ValueError, match="host-registry.toml"):
        certify(remote_pr, config=config)


def test_1495_interrupt_during_lock_grant_releases_unused_host(
    remote_pr: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    statuses, locks = transport(monkeypatch, remote_pr, lock_interrupted=True)
    with pytest.raises(ValueError, match="interrupted by signal"):
        certify(remote_pr)
    assert not statuses
    assert locks == ["lock", "unlock"]


@pytest.mark.parametrize(
    "config_key", ["url.{uri}.insteadOf", "http.https://github.com/.extraHeader"]
)
def test_1495_refuses_repository_local_public_https_overrides(
    tmp_path: Path, config_key: str
) -> None:
    if shutil.disk_usage(tmp_path).free < 30 * 1024**3:
        pytest.skip("Remote bootstrap requires its 30 GiB disk reserve")
    checkout = tmp_path / "src/wgsextract-cli"
    checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "config",
            config_key.format(uri=tmp_path.as_uri()),
            "https://github.com/",
        ],
        check=True,
    )
    run = subprocess.run(
        [sys.executable, "-c", BOOTSTRAP],
        env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode != 0
    assert "unmodified public HTTPS" in run.stderr


def test_972_disconnect_reaps_remote_test_children(tmp_path: Path) -> None:
    if shutil.disk_usage(tmp_path).free < 30 * 1024**3:
        pytest.skip("Remote supervisor integration needs its 30 GiB disk reserve")
    child_script = (
        "import os, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'], "
        "start_new_session=os.name != 'nt'); "
        "print(child.pid, flush=True); time.sleep(300)"
    )
    run_id = "a" * 32
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    supervisor = [sys.executable, "-u", "-c", SUPERVISOR]

    def request(identifier: str, script: str) -> str:
        return json.dumps({"id": identifier, "script": script}) + "\n"

    with subprocess.Popen(
        supervisor,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(request(run_id, child_script))
        process.stdin.flush()
        pid = int(process.stdout.readline())
        retry = subprocess.run(
            supervisor,
            env=env,
            input=request("b" * 32, "print('retry bootstrapped')"),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert retry.returncode == 2
        assert "Another remote certification" in retry.stdout
        assert "retry bootstrapped" not in retry.stdout
        process.stdin.close()
        assert process.wait(timeout=15) == 2
        output = process.stdout.read()
    assert "disconnected" in output
    control = tmp_path / "src/wgsextract-cli/out/certify/remote-control"
    assert (control / (run_id + ".done")).is_file()
    with subprocess.Popen(
        supervisor, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    ) as retry_process:
        assert retry_process.stdin is not None and retry_process.stdout is not None
        retry_process.stdin.write(request("c" * 32, "print('retry bootstrapped')"))
        retry_process.stdin.flush()
        assert retry_process.stdout.readline().strip() == "retry bootstrapped"
        assert retry_process.wait(timeout=15) == 0
    if sys.platform == "win32":
        rows = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], text=True
        )
        assert f'"{pid}"' not in rows
    else:
        status = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            text=True,
            stdout=subprocess.PIPE,
            check=False,
        )
        assert not status.stdout.strip() or status.stdout.strip().startswith("Z")
