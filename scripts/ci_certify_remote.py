"""Coordinate token-free macOS certification over a fixed SSH transport."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from scripts.ci_certifications import (
    CertificationTestFailure,
    gh_api,
    publish_certification,
)
from scripts.ci_certify import validate_head

RESULT = "WGSE_CERTIFICATION_RESULT "
HOST = "randy-mini"
REPOSITORY = "WGSExtract/wgsextract-cli"

# Only literal code enters the remote shell. PR metadata travels as Python data
# over stdin, never as shell arguments (SSH joins its command with spaces).
BOOTSTRAP = """
import os
import pathlib
import shutil
import subprocess
import sys

def check_space():
    if shutil.disk_usage(pathlib.Path.home()).free < 30 * 1024**3:
        raise ValueError("Remote setup needs 30 GiB free, preserving 25 GiB")

check_space()
root = pathlib.Path.home() / "src/wgsextract-cli"
os.chdir(root)
url = "https://github.com/WGSExtract/wgsextract-cli.git"
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
os.environ["PATH"] = str(pathlib.Path.home() / ".pixi/bin") + ":" + os.environ["PATH"]
os.execvp("pixi", ["pixi", "run", "--no-install", "python",
                 "scripts/ci_certify_remote_worker.py", request["sha"],
                 request["base"], request["layer"], str(request["workers"])])
"""


def remote_layer(
    repository: str,
    pr: dict[str, Any],
    sha: str,
    name: str,
    workers: int,
) -> tuple[str, str]:
    for revision in (sha, pr["base"]["sha"]):
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("Remote certification needs full commit SHAs")
    if repository != REPOSITORY or name not in {"macos-arm64", "generic"}:
        raise ValueError("Unsupported remote certification repository/layer")
    request = {"sha": sha, "base": pr["base"]["sha"], "layer": name, "workers": workers}
    script = f"request = {request!r}\n" + BOOTSTRAP
    directory = Path("out/certify") / sha / f"remote-{HOST}" / name
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "run.log"
    last = ""
    command = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "SendEnv=-*",
        HOST,
        "python3 -u -",
    ]
    with log.open("w", encoding="utf-8") as stream:
        stream.write(f"{sha} {name} {HOST}\n")
        stream.flush()
        with subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as process:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(script)
            process.stdin.close()
            for line in process.stdout:
                stream.write(line)
                stream.flush()
                print(line, end="", flush=True)
                last = line
            code = process.wait()
    if not last.startswith(RESULT):
        raise ValueError(f"Remote transport/setup refused (exit {code}); see {log}")
    result = json.loads(last.removeprefix(RESULT))
    if (
        result.get("sha_before") != sha
        or result.get("sha_after") != sha
        or result.get("base") != pr["base"]["sha"]
        or result.get("platform") != "macos-arm64"
        or result.get("layer") != name
    ):
        raise ValueError(f"Remote SHA/platform binding failed; see {log}")
    validate_head(gh_api(f"repos/{repository}/pulls/{pr['number']}"), sha, repository)
    if code == 1 and result.get("state") == "failure":
        raise CertificationTestFailure(code, command)
    if code or result.get("state") != "success":
        raise ValueError(f"Remote certification refused; see {log}")
    digest = hashlib.sha256(log.read_bytes()).hexdigest()
    return f"{HOST} sha256:{digest}", str(log)


def certify_remote(
    repository: str, pr: dict[str, Any], sha: str, args: argparse.Namespace
) -> None:
    layers = ([] if args.generic_only else ["macos-arm64"]) + (
        [] if args.platform_only else ["generic"]
    )
    locked = False
    summaries = []
    try:
        for name in layers:

            def operation(name: str = name) -> tuple[str, str]:
                nonlocal locked
                if not locked:
                    subprocess.run(
                        ["agent-coord", "lock", HOST, "--reason", "972 remote certify"],
                        check=True,
                    )
                    locked = True
                return remote_layer(repository, pr, sha, name, args.workers)

            summary, log = publish_certification(repository, sha, name, operation)
            summaries.append(f"{name}: {summary} (log `{log}`)")
    finally:
        if locked:
            subprocess.run(["agent-coord", "unlock", HOST], check=True)
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
