"""Fixed SSH supervisor with heartbeat, disk reserve and acknowledged cleanup."""

from __future__ import annotations

import json
import shlex
import subprocess
import threading
import uuid
from pathlib import Path

HOST = "randy-mini"

# These are literal programs, not interpolated PR metadata. A single JSON line
# carries the bootstrap; subsequent stdin lines are connection heartbeats.
SUPERVISOR = r"""
import json, os, pathlib, re, select, shutil, signal, subprocess, sys, time

request = json.loads(sys.stdin.readline())
run_id = request["id"]
if not re.fullmatch(r"[a-f0-9]{32}", run_id):
    raise ValueError("Invalid run ID")
control = pathlib.Path.home() / "src/wgsextract-cli/out/certify/remote-control"
control.mkdir(parents=True, exist_ok=True)
started = control / (run_id + ".started")
done = control / (run_id + ".done")
cancel = control / (run_id + ".cancel")
started.touch()
child = None
code = 2
try:
    if cancel.exists():
        raise ValueError("Cancelled before bootstrap")
    if shutil.disk_usage(pathlib.Path.home()).free < 30 * 1024**3:
        raise ValueError("Remote setup needs 30 GiB free, preserving 25 GiB")
    child = subprocess.Popen(
        ["python3", "-u", "-c", request["script"]],
        stdin=subprocess.DEVNULL, start_new_session=True)
    last_seen = time.monotonic()
    while child.poll() is None:
        readable, _, _ = select.select([sys.stdin], [], [], 1)
        if readable:
            if sys.stdin.readline() != "alive\n":
                raise ValueError("Controller disconnected")
            last_seen = time.monotonic()
        if time.monotonic() - last_seen > 30 or cancel.exists():
            raise ValueError("Controller heartbeat expired/cancelled")
        if shutil.disk_usage(pathlib.Path.home()).free < 26 * 1024**3:
            raise ValueError("Remote disk reserve reached; preserving 25 GiB")
    code = child.returncode
except BaseException as error:
    print("Remote supervisor refused:", error, flush=True)
finally:
    if child is not None:
        # Reap all descendants, even when the worker exited before its children.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            # Darwin can report EPERM for a group containing only its unreaped
            # zombie leader. Reap it before signalling any remaining descendants.
            child.poll()
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                break
            time.sleep(1)
        child.wait()
    done.touch()
sys.exit(code)
"""

CANCEL = r"""
import pathlib, re, sys, time

run_id = sys.stdin.readline().strip()
if not re.fullmatch(r"[a-f0-9]{32}", run_id):
    raise ValueError("Invalid run ID")
control = pathlib.Path.home() / "src/wgsextract-cli/out/certify/remote-control"
control.mkdir(parents=True, exist_ok=True)
(control / (run_id + ".cancel")).touch()
deadline = time.monotonic() + 45
while (control / (run_id + ".started")).exists():
    if (control / (run_id + ".done")).exists():
        break
    if time.monotonic() > deadline:
        raise ValueError("Remote termination not confirmed")
    time.sleep(1)
print("stopped")
"""


class RemoteStillRunning(ValueError):
    """Keep the coordinator lock when remote termination cannot be confirmed."""


def ssh_command(program: str) -> list[str]:
    return [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "SendEnv=-*",
        HOST,
        "python3 -u -c " + shlex.quote(program),
    ]


def stream_remote(script: str, log: Path) -> tuple[int, str]:
    run_id = uuid.uuid4().hex
    print(f"Remote control ID: {run_id}", flush=True)
    stop = threading.Event()
    last = ""
    process: subprocess.Popen[str] | None = None
    try:
        with log.open("w", encoding="utf-8") as stream:
            stream.write(f"{HOST} control:{run_id}\n")
            stream.flush()
            process = subprocess.Popen(
                ssh_command(SUPERVISOR),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps({"id": run_id, "script": script}) + "\n")
            process.stdin.flush()

            def heartbeat() -> None:
                assert process is not None and process.stdin is not None
                while not stop.wait(5):
                    try:
                        process.stdin.write("alive\n")
                        process.stdin.flush()
                    except (OSError, ValueError):
                        return

            watcher = threading.Thread(target=heartbeat, daemon=True)
            watcher.start()
            try:
                for line in process.stdout:
                    stream.write(line)
                    stream.flush()
                    print(line, end="", flush=True)
                    last = line
                return process.wait(), last
            finally:
                stop.set()
                watcher.join()
    finally:
        stop.set()
        if process is not None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
        # A fresh connection writes a cancellation tombstone, then waits for
        # the supervisor's process-group cleanup acknowledgement. Never unlock
        # merely because the original SSH connection disappeared.
        try:
            acknowledgement = subprocess.check_output(
                ssh_command(CANCEL),
                input=run_id + "\n",
                text=True,
                timeout=60,
            )
            if acknowledgement.strip() != "stopped":
                raise ValueError("Missing remote cancellation acknowledgement")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise RemoteStillRunning(
                f"Remote stop unconfirmed; {HOST} lock retained. "
                f"Control ID {run_id}; see docs/ci_routing.md"
            ) from error
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if process.stdout is not None:
                    process.stdout.close()
