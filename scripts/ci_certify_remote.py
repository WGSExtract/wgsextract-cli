"""Coordinate token-free native certification over registered SSH hosts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scripts.ci_certifications import (
    CertificationTestFailure,
    gh_api,
    publish_certification,
)
from scripts.ci_certify import validate_head
from scripts.ci_certify_hosts import RemoteHost, hosts_for
from scripts.ci_certify_ssh import (
    RESULT,
    RemoteStillRunning,
    probe_remote,
    stream_remote,
)

REPOSITORY = "WGSExtract/wgsextract-cli"
LOCK_CREATION_FLAGS: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class CertificationInterrupted(ValueError):
    """Interrupt the operation instead of trying the next SSH host."""


# Only literal code enters the remote shell. PR metadata travels as Python data
# over stdin, never as shell arguments (SSH joins its command with spaces).
BOOTSTRAP = """
import os
import pathlib
import shutil
import subprocess
import sys

def check_space():
    if shutil.disk_usage(pathlib.Path.cwd()).free < 30 * 1024**3:
        raise ValueError("Remote setup needs 30 GiB free, preserving 25 GiB")

root = pathlib.Path.home() / "src/wgsextract-cli"
os.chdir(root)
check_space()
for key in list(os.environ):
    if key.startswith(("GH_", "GITHUB_", "GIT_")):
        del os.environ[key]
os.environ.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
                  GIT_CONFIG_GLOBAL=os.devnull)
os.environ["PATH"] = str(pathlib.Path.home() / ".pixi/bin") + os.pathsep + os.environ["PATH"]
url = "https://github.com/WGSExtract/wgsextract-cli.git"
effective_url = subprocess.check_output(["git", "ls-remote", "--get-url", url],
                                        text=True).strip()
http_config = subprocess.run(["git", "config", "--name-only", "--get-regexp",
                              "^http[.]"], capture_output=True, text=True)
if effective_url != url or http_config.returncode != 1:
    raise ValueError("Remote checkout must use unmodified public HTTPS without HTTP overrides")
for revision in (request["sha"], request["base"]):
    subprocess.run(["git", "-c", "credential.helper=", "fetch", "--no-tags",
                    url, revision], check=True, stdin=subprocess.DEVNULL)
worktree = root / "scratch" / ("certify-" + request["sha"])
if not worktree.exists():
    subprocess.run(["git", "worktree", "add", "--detach",
                    str(worktree), request["sha"]], check=True)
os.chdir(worktree)
check_space()
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
dirty = subprocess.check_output(
    ["git", "status", "--porcelain", "--untracked-files=all"], text=True)
if head != request["sha"] or dirty:
    raise ValueError("Remote checkout is dirty or HEAD changed before execution")
command = ["pixi", "run", "--no-install", "python", "-X", "utf8",
           "scripts/ci_certify_remote_worker.py", request["sha"],
           request["base"], request["layer"], request["platform"], str(request["workers"])]
coordinator = shutil.which("agent-coord")
if coordinator:
    command = [coordinator, "run", "--cores", str(request["workers"]), "--mem", "8G",
               "--lock", "wgsextract-certification", "--label", "remote certification",
               "--", *command]
sys.exit(subprocess.run(command, stdin=subprocess.DEVNULL).returncode)
"""


def remote_layer(
    repository: str,
    pr: dict[str, Any],
    sha: str,
    name: str,
    workers: int,
    host: RemoteHost,
) -> tuple[str, str]:
    for revision in (sha, pr["base"]["sha"]):
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("Remote certification needs full commit SHAs")
    if repository != REPOSITORY or name not in {host.platform, "generic"}:
        raise ValueError("Unsupported remote certification repository/layer")
    request = {
        "sha": sha,
        "base": pr["base"]["sha"],
        "layer": name,
        "platform": host.platform,
        "workers": workers,
    }
    script = f"request = {request!r}\n" + BOOTSTRAP
    directory = Path("out/certify") / sha / f"remote-{host.alias}" / name
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "run.log"
    code, message = stream_remote(host, script, log)
    if not message:
        raise ValueError(f"Remote transport/setup refused (exit {code}); see {log}")
    result = json.loads(message.removeprefix(RESULT))
    if (
        result.get("sha_before") != sha
        or result.get("sha_after") != sha
        or result.get("base") != pr["base"]["sha"]
        or result.get("platform") != host.platform
        or result.get("layer") != name
    ):
        raise ValueError(f"Remote SHA/platform binding failed; see {log}")
    current = gh_api(f"repos/{repository}/pulls/{pr['number']}")
    validate_head(current, sha, repository)
    if current["base"]["sha"] != pr["base"]["sha"]:
        raise ValueError("PR base changed during remote certification")
    if code == 1 and result.get("state") == "failure":
        raise CertificationTestFailure(code, ["ssh", host.alias])
    if code or result.get("state") != "success":
        raise ValueError(f"Remote certification refused; see {log}")
    digest = hashlib.sha256(log.read_bytes()).hexdigest()
    return f"{host.alias} sha256:{digest}", str(log)


@contextmanager
def available_host(hosts: list[RemoteHost]) -> Iterator[RemoteHost]:
    refused = []
    for host in hosts:
        locked = False
        try:
            try:
                probe_remote(host)
                interrupted: list[int] = []

                def defer(
                    signum: int, frame: Any, pending: list[int] = interrupted
                ) -> None:
                    pending.append(signum)

                # Learn whether the coordinator granted the lock before acting
                # on a signal delivered while the command was in flight.
                previous = signal.signal(signal.SIGTERM, defer)
                previous_int = signal.signal(signal.SIGINT, defer)
                try:
                    subprocess.run(
                        [
                            "agent-coord",
                            "lock",
                            host.alias,
                            "--reason",
                            "remote certification",
                        ],
                        check=True,
                        start_new_session=sys.platform != "win32",
                        creationflags=LOCK_CREATION_FLAGS,
                    )
                    locked = True
                finally:
                    signal.signal(signal.SIGINT, previous_int)
                    signal.signal(signal.SIGTERM, previous)
                    if interrupted:
                        raise CertificationInterrupted(
                            f"Certification interrupted by signal {interrupted[0]}"
                        )
            except CertificationInterrupted:
                raise
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                refused.append(f"{host.alias}: {error}")
                continue
            yield host
        except RemoteStillRunning:
            locked = False
            raise
        finally:
            if locked:
                subprocess.run(["agent-coord", "unlock", host.alias], check=True)
        return
    raise ValueError("No available certification host:\n" + "\n".join(refused))


def certify_remote(
    repository: str, pr: dict[str, Any], sha: str, args: argparse.Namespace
) -> None:
    if repository != REPOSITORY:
        raise ValueError("Unsupported remote certification repository")
    hosts = hosts_for(args.remote, args.remote_config)
    summaries = []

    def interrupted(signum: int, frame: Any) -> None:
        raise CertificationInterrupted(f"Certification interrupted by signal {signum}")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        with available_host(hosts) as host:
            layers = ([] if args.generic_only else [host.platform]) + (
                [] if args.platform_only else ["generic"]
            )
            for name in layers:

                def operation(name: str = name) -> tuple[str, str]:
                    return remote_layer(repository, pr, sha, name, args.workers, host)

                summary, log = publish_certification(repository, sha, name, operation)
                summaries.append(f"{name}: {summary} (log `{log}`)")
    finally:
        signal.signal(signal.SIGTERM, previous)
    subprocess.run(
        [
            "gh",
            "pr",
            "comment",
            str(pr["number"]),
            "--body",
            f"(AI): remotely self-certified `{sha}` — " + "; ".join(summaries),
        ],
        check=True,
    )
