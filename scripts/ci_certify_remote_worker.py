"""Token-free worker; only the SSH controller publishes GitHub statuses."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_certifications import CertificationTestFailure  # noqa: E402
from scripts.ci_certify import (  # noqa: E402
    changed_paths,
    generic_commands,
    git,
    host_platform,
    platform_commands,
    required_environments,
    run_layer,
    selection,
)
from scripts.ci_certify_inputs import validate_environments, validate_reference  # noqa: E402
from scripts.ci_certify_remote import RESULT  # noqa: E402
from scripts.ci_select_tests import select_tests  # noqa: E402


def verify_checkout(sha: str) -> str:
    head = git("rev-parse", "HEAD")
    if head != sha or git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Remote checkout is dirty or HEAD changed")
    if host_platform() != "macos-arm64":
        raise ValueError("Remote machine is not macos-arm64")
    if shutil.disk_usage(Path.cwd()).free < 26 * 1024**3:
        raise ValueError("Remote disk reserve: keep at least 25 GiB free")
    return head


def execute(sha: str, base: str, name: str, workers: int) -> None:
    verify_checkout(sha)
    paths = changed_paths(base, sha)
    cli, targets = selection(paths, "macos-arm64")
    environments = required_environments(name, "macos-arm64", cli, targets)
    setup = ["pixi", "run", "--no-install", "python", "scripts/ci_certify_setup.py"]
    for environment in environments:
        setup += ["-e", environment]
    try:
        validate_environments(environments)
    except ValueError:
        subprocess.run(setup, check=True, stdin=subprocess.DEVNULL)
    if name != "generic" and cli and targets:
        try:
            validate_reference()
        except ValueError:
            subprocess.run(
                [*setup, "--reference"], check=True, stdin=subprocess.DEVNULL
            )
    verify_checkout(sha)
    commands = (
        generic_commands(paths, select_tests(paths))
        if name == "generic"
        else platform_commands("macos-arm64", targets, not cli)
    )
    run_layer(name, commands, sha, workers, stream_output=True)
    verify_checkout(sha)


def main() -> int:
    import json

    sha, base, name, worker_count = sys.argv[1:]
    result = {
        "sha_before": "",
        "sha_after": "",
        "base": base,
        "platform": host_platform(),
        "layer": name,
        "state": "error",
    }
    code = 2
    try:
        result["sha_before"] = verify_checkout(sha)
        execute(sha, base, name, int(worker_count))
        result["state"], code = "success", 0
    except CertificationTestFailure as error:
        print(error, flush=True)
        result["state"], code = "failure", 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Remote certification refused: {error}", flush=True)
    finally:
        try:
            result["sha_after"] = verify_checkout(sha)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(error, flush=True)
            result["state"], code = "error", 2
        print(RESULT + json.dumps(result), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
