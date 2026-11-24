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
from scripts.ci_certify_ssh import RESULT  # noqa: E402
from scripts.ci_select_tests import select_tests  # noqa: E402


def verify_checkout(sha: str, expected_platform: str) -> str:
    head = git("rev-parse", "HEAD")
    if head != sha or git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Remote checkout is dirty or HEAD changed")
    if host_platform() != expected_platform:
        raise ValueError(f"Remote machine is not {expected_platform}")
    if shutil.disk_usage(Path.cwd()).free < 26 * 1024**3:
        raise ValueError("Remote disk reserve: keep at least 25 GiB free")
    return head


def execute(sha: str, base: str, name: str, platform: str, workers: int) -> None:
    verify_checkout(sha, platform)
    paths = changed_paths(base, sha)
    cli, targets = selection(paths, platform)
    environments = required_environments(name, platform, cli, targets)
    setup = ["pixi", "run", "--no-install", "python", "scripts/ci_certify_setup.py"]
    for environment in environments:
        setup += ["-e", environment]
    try:
        validate_environments(environments)
    except ValueError:
        subprocess.run(setup, check=True, stdin=subprocess.DEVNULL)
        validate_environments(environments)
    if (
        name != "generic"
        and cli
        and targets
        and platform
        in {
            "linux-x64",
            "macos-arm64",
            "macos-x64",
        }
    ):
        try:
            validate_reference()
        except ValueError:
            subprocess.run(
                [*setup, "--reference"], check=True, stdin=subprocess.DEVNULL
            )
    verify_checkout(sha, platform)
    commands = (
        generic_commands(paths, select_tests(paths))
        if name == "generic"
        else platform_commands(platform, targets, not cli)
    )
    run_layer(name, commands, sha, workers, stream_output=True)
    verify_checkout(sha, platform)


def main() -> int:
    import json

    sha, base, name, platform, worker_count = sys.argv[1:]
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
        if name not in {platform, "generic"}:
            raise ValueError(f"Unsupported remote layer {name}")
        result["sha_before"] = verify_checkout(sha, platform)
        execute(sha, base, name, platform, int(worker_count))
        result["state"], code = "success", 0
    except CertificationTestFailure as error:
        print(error, flush=True)
        result["state"], code = "failure", 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Remote certification refused: {error}", flush=True)
    finally:
        try:
            result["sha_after"] = verify_checkout(sha, platform)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(error, flush=True)
            result["state"], code = "error", 2
        print(RESULT + json.dumps(result), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
