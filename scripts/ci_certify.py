"""Certify the pushed PR head's selected host tests and generic checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_certifications import WRITERS, gh_api  # noqa: E402
from scripts.ci_changes import routes, site_validation  # noqa: E402
from scripts.ci_select_tests import CI_TESTS, os_selection, select_tests  # noqa: E402
from scripts.ci_test_dependencies import docs_test_targets  # noqa: E402


def host_platform() -> str:
    system = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(
        platform.system()
    )
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine().lower()
    )
    if not system or not arch or (system == "windows" and arch != "x64"):
        raise ValueError("Unsupported certification host")
    return f"{system}-{arch}"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def validate_head(pr: dict[str, Any], sha: str, repository: str) -> None:
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Refusing dirty tree")
    if pr.get("head", {}).get("repo", {}).get("full_name") != repository:
        raise ValueError("Refusing fork PR")
    if pr.get("state") != "open" or pr["head"]["sha"] != sha:
        raise ValueError("HEAD is not the pushed open PR head")
    if git("rev-parse", "HEAD") != sha:
        raise ValueError("Local HEAD changed during certification")
    branch = git("branch", "--show-current")
    if not branch or branch != pr["head"]["ref"]:
        raise ValueError("Checkout must be on the pushed PR branch")
    remote = git("ls-remote", "origin", f"refs/heads/{branch}")
    if not remote or remote.split()[0] != sha:
        raise ValueError("Origin branch does not match HEAD")


def pr_head() -> tuple[str, dict[str, Any], str]:
    remote = json.loads(
        subprocess.check_output(
            ["gh", "repo", "view", "--json", "nameWithOwner,isFork"], text=True
        )
    )
    if remote["isFork"]:
        raise ValueError("Refusing fork repository")
    repository = remote["nameWithOwner"]
    number = json.loads(
        subprocess.check_output(["gh", "pr", "view", "--json", "number"], text=True)
    )["number"]
    pr = gh_api(f"repos/{repository}/pulls/{number}")
    sha = git("rev-parse", "HEAD")
    validate_head(pr, sha, repository)
    user = gh_api("user")["login"]
    if (
        gh_api(f"repos/{repository}/collaborators/{user}/permission").get("permission")
        not in WRITERS
    ):
        raise ValueError("Certification requires repository write permission")
    return repository, pr, sha


def run_layer(
    name: str, commands: list[list[str]], sha: str, workers: int
) -> tuple[str, str]:
    directory = Path("out/certify") / sha / name
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "run.log"
    started = time.monotonic()
    count = 0
    with log.open("w", encoding="utf-8") as stream:
        stream.write(f"{sha} {name} {platform.node()}\n")
        for index, command in enumerate(commands):
            xml = directory / f"tests-{index}.xml"
            pytest_command = command[:4] == [
                "pixi",
                "run",
                "--no-install",
                "pytest",
            ] or command[:5] == [
                "pixi",
                "run",
                "--no-install",
                "python",
                "scripts/ci_run_tests.py",
            ]
            if pytest_command:
                command = [
                    *command,
                    "-n",
                    str(workers),
                    f"--junitxml={xml}",
                    f"--basetemp={directory / f'tmp-{index}'}",
                ]
            stream.write(f"\n$ {json.dumps(command)}\n")
            stream.flush()
            print(f"{name}: {' '.join(command[:7])}", flush=True)
            subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True)
            if xml.exists():
                cases = list(ET.parse(xml).iter("testcase"))
                count += sum(case.find("skipped") is None for case in cases)
                no_skips = "pytest" in command and any(
                    name in command
                    for name in (
                        "tests/test_imputation.py",
                        "tests/test_ont_hg002.py",
                        "tests/test_vep_rs.py",
                    )
                )
                if no_skips and (
                    not cases or any(case.find("skipped") is not None for case in cases)
                ):
                    raise ValueError(
                        f"CI's no-skips suite needs installed tools: {xml}"
                    )
        duration = round(time.monotonic() - started)
        stream.write(f"\nExecuted {count} pytest tests in {duration}s\n")
    digest = hashlib.sha256(log.read_bytes()).hexdigest()
    # GitHub status descriptions are limited to 140 characters.
    summary = f"{platform.node()[:25]} {count} pytest {duration}s sha256:{digest}"
    return summary, str(log)


def platform_commands(
    host: str, targets: list[str] | str, smoke: bool
) -> list[list[str]]:
    selection = targets if isinstance(targets, str) else json.dumps(targets)
    pixi = ["pixi", "run", "--no-install"]
    commands: list[list[str]] = []
    if not targets:
        return commands
    if not smoke:
        environments = {
            "linux-x64": ["default", "dev", "vep-rs"],
            "macos-arm64": ["default", "dev", "vep-rs"],
            "macos-x64": ["default", "dev", "vep-rs"],
            "windows-x64": ["default", "dev", "gatk", "vep-rs"],
            "linux-arm64": ["default", "dev", "vep"],
        }[host]
        commands.append(
            [
                *pixi,
                "python",
                "-c",
                "from pathlib import Path; "
                f"names={environments!r}; "
                "missing=[n for n in names if not Path('.pixi/envs', n).is_dir()]; "
                "assert not missing, f'Required CI environments missing: {missing}'",
            ]
        )
        if "vep-rs" in environments:
            commands.append(
                [
                    *pixi,
                    "python",
                    "-c",
                    "from wgsextract_cli.core.dependencies import get_tool_path as g; "
                    "assert g('vep-rs') and g('vep-cache-builder'), "
                    "'Required CI VEP-rs tools missing'",
                ]
            )
        if host.startswith("windows"):
            commands.append(
                [
                    "cmd",
                    "/c",
                    "install_windows.bat",
                    "--dry-run",
                    "--msys2-root",
                    r"C:\msys64",
                    "--skip-pacman-setup",
                ]
            )
        else:
            commands.append(["bash", "scripts/test_install_sh.sh"])
        if host in {"linux-x64", "macos-arm64", "macos-x64"}:
            commands.append([*pixi, "bash", "scripts/verify_tools.sh"])
        else:
            commands.append([*pixi, "wgsextract", "deps", "check"])
        if host == "linux-arm64":
            commands.extend(
                [
                    [*pixi, "wgsextract", "lineage", "y-haplogroup", "--help"],
                    ["pixi", "run", "-e", "vep", "--no-install", "vep", "--help"],
                ]
            )
    commands.append(
        [
            *pixi,
            "python",
            "scripts/ci_run_tests.py",
            "--selection",
            selection,
            *([] if smoke else ["--platform-only"]),
            "--",
            "--ci-tier",
            "all",
            "--ci-shard",
            "all",
        ]
    )
    if smoke:
        return commands
    all_tests = targets == "all"
    if all_tests or any("test_e2e_" in target for target in targets):
        commands.append(
            [
                *pixi,
                "pytest",
                "--fake-data",
                "tests/test_e2e_fast_chrM.py",
                "tests/test_e2e_full_genome.py",
            ]
        )
    if host == "linux-x64":
        if all_tests or "tests/test_imputation.py" in targets:
            commands.append([*pixi, "pytest", "tests/test_imputation.py", "-rs"])
        ont = ("test_ont_hg002.py", "test_phasing.py", "test_somalier_relate.py")
        if all_tests or any(f"tests/{test}" in targets for test in ont):
            commands.append(
                [
                    *pixi,
                    "pytest",
                    *(f"tests/{test}" for test in ont),
                    "-k",
                    "not clair3",
                    "-rs",
                ]
            )
    if host == "linux-x64" and (all_tests or "tests/test_cli_parser.py" in targets):
        commands.append(
            [
                *pixi,
                "python",
                "-m",
                "unittest",
                "discover",
                "-s",
                "gui/tests/wgsextract",
            ]
        )
    if host == "windows-x64":
        for test in ("tests/test_imputation.py", "tests/test_vep_rs.py"):
            if all_tests or test in targets:
                commands.append(
                    [
                        *pixi,
                        "pytest",
                        test,
                        "-p",
                        "no:randomly",
                        "-rs",
                        "-o",
                        "addopts=",
                    ]
                )
    return commands


def generic_commands(paths: list[str], targets: list[str] | str) -> list[list[str]]:
    pixi = ["pixi", "run", "--no-install"]
    commands = [
        [*pixi, "ruff", "check", "."],
        [*pixi, "mypy", "src/wgsextract_cli"],
        [*pixi, "actionlint"],
        [*pixi, "python", "scripts/validate_github_actions.py"],
        [*pixi, "python", "scripts/validate_site_sources.py"],
        [*pixi, "pytest", *CI_TESTS],
    ]
    python_files = [
        path for path in paths if path.endswith(".py") and Path(path).is_file()
    ]
    cli_files = [path for path in python_files if not path.startswith("gui/")]
    gui_files = [path for path in python_files if path.startswith("gui/")]
    if cli_files:
        commands.extend(
            [
                [*pixi, "ruff", "check", "--no-force-exclude", "--", *cli_files],
                [
                    *pixi,
                    "ruff",
                    "format",
                    "--check",
                    "--no-force-exclude",
                    "--",
                    *cli_files,
                ],
            ]
        )
    if gui_files:
        commands.extend(
            [
                [*pixi, "python", "-m", "compileall", "-q", *gui_files],
                [
                    *pixi,
                    "ruff",
                    "check",
                    "--isolated",
                    "--target-version",
                    "py313",
                    "--select",
                    "E9,F63,F7,F82",
                    "--",
                    *gui_files,
                ],
            ]
        )
    docs = docs_test_targets(paths, cli=routes(paths)["cli"])
    if docs:
        commands.append([*pixi, "pytest", *docs])
    if site_validation(paths) or routes(paths)["site"]:
        commands.append([*pixi, "python", "gui/scripts/validate_site_sources.py"])
    if routes(paths)["site"]:
        for script in ("scripts/build_site.py", "gui/scripts/build_site.py"):
            commands.append([*pixi, "python", script])
        commands.append(
            [
                *pixi,
                "python",
                "-c",
                "from pathlib import Path; assert Path('gui/mockups').is_dir()",
            ]
        )
    commands.append(
        [
            *pixi,
            "python",
            "scripts/ci_run_tests.py",
            "--generic-only",
            "--selection",
            targets if isinstance(targets, str) else json.dumps(targets),
            "--",
        ]
    )
    return commands


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--platform-only", action="store_true")
    mode.add_argument("--generic-only", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    repository, pr, sha = pr_head()
    host = host_platform()
    subprocess.run(["git", "fetch", "origin", pr["base"]["sha"]], check=True)
    changed = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            f"{pr['base']['sha']}...{sha}",
            "--",
        ]
    )
    paths = changed.decode().rstrip("\0").split("\0") if changed else []
    selected = routes(paths)
    targets = select_tests(paths)
    os = {
        "linux-x64": "ubuntu",
        "linux-arm64": "arm64",
        "macos-arm64": "macos",
        "macos-x64": "macos",
        "windows-x64": "windows",
    }[host]
    platform_targets = os_selection(paths, targets, cli=selected["cli"])[os]
    layers = {}
    if not args.generic_only:
        layers[host] = platform_commands(host, platform_targets, not selected["cli"])
    if not args.platform_only:
        layers["generic"] = generic_commands(paths, targets)
    summaries = []
    for name, commands in layers.items():
        endpoint = f"repos/{repository}/statuses/{sha}"
        subprocess.run(
            [
                "gh",
                "api",
                endpoint,
                "-f",
                "state=pending",
                "-f",
                f"context=local-ci/{name}",
                "-f",
                "description=Local certification running",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        try:
            summary, log = run_layer(name, commands, sha, args.workers)
        except (subprocess.SubprocessError, ValueError):
            subprocess.run(
                [
                    "gh",
                    "api",
                    endpoint,
                    "-f",
                    "state=failure",
                    "-f",
                    f"context=local-ci/{name}",
                    "-f",
                    "description=Local certification failed; see out/certify",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            raise
        validate_head(
            gh_api(f"repos/{repository}/pulls/{pr['number']}"), sha, repository
        )
        subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/statuses/{sha}",
                "-f",
                "state=success",
                "-f",
                f"context=local-ci/{name}",
                "-f",
                f"description={summary}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        summaries.append(f"{name}: {summary} (log `{log}`)")
    subprocess.run(
        [
            "gh",
            "pr",
            "comment",
            str(pr["number"]),
            "--body",
            f"(AI): self-certified `{sha}` — " + "; ".join(summaries),
        ],
        check=True,
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Certification refused/failed: {error}") from error
