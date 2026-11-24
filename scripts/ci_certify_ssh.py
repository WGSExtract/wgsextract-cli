"""Fixed SSH supervisor with heartbeat, disk reserve and acknowledged cleanup."""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import shlex
import subprocess
import threading
import uuid
from pathlib import Path

from scripts.ci_certify_hosts import RemoteHost

RESULT = "WGSE_CERTIFICATION_RESULT "
REMOTE_ROOT = 'pathlib.Path.home() / "src/wgsextract-cli"'
LOADER = "import sys; exec(sys.stdin.readline())"

PROBE = f"""
import os, pathlib, platform, shutil

system = {{"Linux": "linux", "Darwin": "macos", "Windows": "windows"}}.get(platform.system())
arch = {{"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}}.get(platform.machine().lower())
root = {REMOTE_ROOT}
if not (root / ".git").exists():
    raise ValueError(f"Prepare the isolated certification checkout at {{root}}")
os.environ["PATH"] = str(pathlib.Path.home() / ".pixi/bin") + os.pathsep + os.environ["PATH"]
if not shutil.which("git") or not shutil.which("pixi"):
    raise ValueError("Install Git and the pinned Pixi in the remote user's PATH")
if shutil.disk_usage(root).free < 30 * 1024**3:
    raise ValueError("Remote setup needs 30 GiB free, preserving 25 GiB")
print(f"WGSE_READY {{system}}-{{arch}}")
"""

# These are literal programs, not interpolated PR metadata. A single JSON line
# carries the bootstrap; subsequent stdin lines are connection heartbeats.
SUPERVISOR = r"""
import json, os, pathlib, queue, re, shutil, signal, subprocess, sys, threading, time

class WindowsJob:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w

        class BasicLimits(c.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", c.c_longlong),
                ("PerJobUserTimeLimit", c.c_longlong),
                ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", c.c_size_t),
                ("MaximumWorkingSetSize", c.c_size_t),
                ("ActiveProcessLimit", w.DWORD),
                ("Affinity", c.c_size_t),
                ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD),
            ]

        class ExtendedLimits(c.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", c.c_ulonglong * 6),
                ("ProcessMemoryLimit", c.c_size_t),
                ("JobMemoryLimit", c.c_size_t),
                ("PeakProcessMemoryUsed", c.c_size_t),
                ("PeakJobMemoryUsed", c.c_size_t),
            ]

        class Accounting(c.Structure):
            _fields_ = [
                ("Times", c.c_longlong * 4),
                ("TotalPageFaultCount", w.DWORD),
                ("TotalProcesses", w.DWORD),
                ("ActiveProcesses", w.DWORD),
                ("TotalTerminatedProcesses", w.DWORD),
            ]

        self.api = c.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.api.OpenProcess.restype = w.HANDLE
        self.api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.api.QueryInformationJobObject.argtypes = [
            w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD),
        ]
        self.api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.error, self.byref, self.sizeof = c.WinError, c.byref, c.sizeof
        self.accounting = Accounting()
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise self.error()
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            self.api.CloseHandle(self.handle)
            raise self.error()

    def assign(self, pid):
        handle = self.api.OpenProcess(0x101, False, pid)  # SET_QUOTA | TERMINATE
        if not handle:
            raise self.error()
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise self.error()
        finally:
            self.api.CloseHandle(handle)

    def stop(self):
        try:
            if not self.api.TerminateJobObject(self.handle, 2):
                raise self.error()
            deadline = time.monotonic() + 10
            while True:
                if not self.api.QueryInformationJobObject(
                    self.handle, 1, self.byref(self.accounting), self.sizeof(self.accounting), None,
                ):
                    raise self.error()
                if self.accounting.ActiveProcesses == 0:
                    return
                if time.monotonic() >= deadline:
                    raise ValueError("Windows job termination not confirmed")
                time.sleep(0.1)
        finally:
            self.api.CloseHandle(self.handle)

request = json.loads(sys.stdin.readline())
run_id = request["id"]
if not re.fullmatch(r"[a-f0-9]{32}", run_id):
    raise ValueError("Invalid run ID")
root = pathlib.Path.home() / "src/wgsextract-cli"
control = root / "out/certify/remote-control"
control.mkdir(parents=True, exist_ok=True)
started = control / (run_id + ".started")
done = control / (run_id + ".done")
cancel = control / (run_id + ".cancel")
started.touch()
child = None
job = None
assigned = False
code = 2

def listen():
    for line in sys.stdin:
        heartbeats.put(line)
    heartbeats.put("")

heartbeats = queue.Queue()
threading.Thread(target=listen, daemon=True).start()
observed = {}

def posix_processes():
    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,pgid=,stat=,lstart="], text=True)
    return {int(pid): (int(parent), int(group), state, started)
            for pid, parent, group, state, started in
            (row.split(maxsplit=4) for row in rows.splitlines())}

def active_posix_groups():
    processes = posix_processes()
    # A descendant that changed groups or was reparented is still ours only
    # while its PID identifies the same live process.
    descendants = {
        pid for pid, (_, _, state, started) in processes.items()
        if not state.startswith("Z") and observed.get(pid) == started
    }
    if child.poll() is None and child.pid in processes:
        descendants.add(child.pid)
    while True:
        discovered = {pid for pid, (parent, _, state, _) in processes.items()
                      if parent in descendants and pid not in descendants
                      and not state.startswith("Z")}
        if not discovered:
            break
        descendants.update(discovered)
    observed.clear()
    observed.update({pid: processes[pid][3] for pid in descendants})
    return {processes[pid][1] for pid in descendants}

try:
    if cancel.exists():
        raise ValueError("Cancelled before bootstrap")
    if shutil.disk_usage(root).free < 30 * 1024**3:
        raise ValueError("Remote setup needs 30 GiB free, preserving 25 GiB")
    if os.name == "nt":
        job = WindowsJob()
    # Keep the child blocked on stdin until it is in its kill-on-close job.
    child = subprocess.Popen(
        [sys.executable, "-I", "-X", "utf8", "-u", "-c",
         "import sys; exec(sys.stdin.readline())"],
        stdin=subprocess.PIPE, text=True, start_new_session=(job is None))
    if job is not None:
        job.assign(child.pid)
        assigned = True
    with child.stdin:
        child.stdin.write("exec(" + repr(request["script"]) + ")\n")
    last_seen = time.monotonic()
    while child.poll() is None:
        if job is None:
            active_posix_groups()
        try:
            if heartbeats.get(timeout=1) != "alive\n":
                raise ValueError("Controller disconnected")
            last_seen = time.monotonic()
        except queue.Empty:
            pass
        if time.monotonic() - last_seen > 30 or cancel.exists():
            raise ValueError("Controller heartbeat expired/cancelled")
        if shutil.disk_usage(root).free < 26 * 1024**3:
            raise ValueError("Remote disk reserve reached; preserving 25 GiB")
    code = child.returncode
except BaseException as error:
    print("Remote supervisor refused:", error, flush=True)
finally:
    if job is not None:
        if child is not None and not assigned:
            child.kill()
        job.stop()
    if child is not None:
        if job is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                groups = active_posix_groups()
                if not groups:
                    break
                for group in groups:
                    try:
                        os.killpg(group, sig)
                    except ProcessLookupError:
                        pass
                time.sleep(1)
            if active_posix_groups():
                raise ValueError("POSIX process-group termination not confirmed")
        child.wait()
    done.touch()
sys.stdout.flush()
# Don't tear down a buffered stdin reader still blocked on an open SSH pipe.
os._exit(code)
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


def ssh_command(host: RemoteHost) -> list[str]:
    if host.windows:
        # OpenSSH may start cmd or PowerShell. Use one fixed encoded command;
        # Windows needs only the Python from the prepared controller checkout.
        command = (
            "$ProgressPreference = 'SilentlyContinue'; "
            "$root = Join-Path $HOME 'src/wgsextract-cli'; "
            "if (!(Test-Path -LiteralPath (Join-Path $root '.pixi/envs/dev/python.exe'))) { "
            'throw "Prepare the locked dev environment in ~/src/wgsextract-cli" }; '
            "Set-Location -LiteralPath $root; "
            f'& pixi run --no-install -e dev python -I -X utf8 -u -c "{LOADER}"; exit $LASTEXITCODE'
        )
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        launcher = (
            f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {encoded}"
        )
    else:
        launcher = "python3 -I -X utf8 -u -c " + shlex.quote(LOADER)
    return [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ForwardX11=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "SendEnv=-*",
        host.alias,
        launcher,
    ]


def program_line(program: str) -> str:
    return f"exec({program!r})\n"


def ssh_environment() -> dict[str, str]:
    # SSH may inherit connection settings, but never controller credentials.
    # SSH_AUTH_SOCK authenticates the connection; agent forwarding is disabled.
    return {
        name: value
        for name, value in os.environ.items()
        if name.upper()
        in {"PATH", "HOME", "USERPROFILE", "SSH_AUTH_SOCK", "SYSTEMROOT", "WINDIR"}
    }


def probe_remote(host: RemoteHost) -> None:
    command = ssh_command(host)
    environment = ssh_environment()
    options = subprocess.check_output(
        [*command[:-2], "-G", host.alias], env=environment, text=True, timeout=15
    )
    for line in options.splitlines():
        name, _, value = line.partition(" ")
        if name == "setenv" or (
            name == "sendenv"
            and any(fnmatch.fnmatchcase(key, value) for key in environment)
        ):
            raise ValueError(
                f"{host.alias} SSH configuration forwards environment data"
            )
    result = subprocess.run(
        command,
        env=environment,
        input=program_line(PROBE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=20,
        check=False,
    )
    if (
        result.returncode
        or f"WGSE_READY {host.platform}" not in result.stdout.splitlines()
    ):
        raise ValueError(
            f"{host.alias} is unavailable for {host.platform}: {result.stdout.strip()}"
        )


def stream_remote(host: RemoteHost, script: str, log: Path) -> tuple[int, str]:
    run_id = uuid.uuid4().hex
    print(f"Remote control ID: {run_id}", flush=True)
    stop = threading.Event()
    result = ""
    process: subprocess.Popen[str] | None = None
    try:
        with log.open("w", encoding="utf-8") as stream:
            stream.write(f"{host.alias} control:{run_id}\n")
            stream.flush()
            process = subprocess.Popen(
                ssh_command(host),
                env=ssh_environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(
                program_line(SUPERVISOR)
                + json.dumps({"id": run_id, "script": script})
                + "\n"
            )
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
                    if line.startswith(RESULT):
                        result = line
                return process.wait(), result
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
                ssh_command(host),
                env=ssh_environment(),
                input=program_line(CANCEL) + run_id + "\n",
                text=True,
                timeout=60,
            )
            if acknowledgement.strip() != "stopped":
                raise ValueError("Missing remote cancellation acknowledgement")
        except BaseException as error:
            raise RemoteStillRunning(
                f"Remote stop unconfirmed; {host.alias} lock retained. "
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
