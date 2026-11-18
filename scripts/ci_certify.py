"""Certify the pushed PR head's selected host tests and generic checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_certifications import (  # noqa: E402
    WRITERS,
    CertificationTestFailure,
    gh_api,
    publish_certification,
)
from scripts.ci_certify_inputs import validate_environments  # noqa: E402
from scripts.ci_changes import documentation_targets, routes, site_validation  # noqa: E402
from scripts.ci_select_tests import ci_test_targets, os_selection, select_tests  # noqa: E402


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


def pytest_basetemp(directory: Path, sha: str, name: str, index: int) -> Path:
    """Where a layer's pytest run keeps its temporary files.

    samtools, bcftools and other native Windows tools fail on paths over 260
    characters, which a worktree's out/certify/<sha>/<layer> folder plus
    pytest's per-test folders exceed, so on Windows tests run under the
    system temporary folder, as on CI's runners.
    """
    if sys.platform == "win32":
        root = Path(tempfile.gettempdir(), "wgse-certify", sha[:12])
        root.mkdir(parents=True, exist_ok=True)  # pytest makes only the leaf.
        return root / f"{name}-{index}"
    return directory / f"tmp-{index}"


def run_layer(
    name: str,
    commands: list[list[str]],
    sha: str,
    workers: int,
    *,
    stream_output: bool = False,
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
            pytest_command = (
                command[1:3] == ["-m", "pytest"]
                or command[:4]
                == [
                    "pixi",
                    "run",
                    "--no-install",
                    "pytest",
                ]
                or command[:5]
                == [
                    "pixi",
                    "run",
                    "--no-install",
                    "python",
                    "scripts/ci_run_tests.py",
                ]
            )
            if pytest_command:
                xml.unlink(missing_ok=True)
                command = [
                    *command,
                    "-n",
                    str(workers),
                    f"--junitxml={xml}",
                    f"--basetemp={pytest_basetemp(directory, sha, name, index)}",
                ]
            cwd = (
                "gui"
                if command[:6]
                == [
                    "pixi",
                    "run",
                    "--no-install",
                    "python",
                    "-m",
                    "unittest",
                ]
                else None
            )
            stream.write(f"\n$ {json.dumps(command)} (cwd={cwd or '.'})\n")
            stream.flush()
            print(f"{name}: {' '.join(command[:7])}", flush=True)
            try:
                if stream_output:
                    with subprocess.Popen(
                        command,
                        cwd=cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    ) as process:
                        assert process.stdout is not None
                        for line in process.stdout:
                            stream.write(line)
                            print(line, end="", flush=True)
                        if process.wait():
                            raise subprocess.CalledProcessError(
                                process.returncode, command
                            )
                else:
                    subprocess.run(
                        command,
                        cwd=cwd,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        check=True,
                    )
            except subprocess.CalledProcessError as error:
                if pytest_command and xml.is_file():
                    if any(
                        case.find("failure") is not None
                        for case in ET.parse(xml).iter("testcase")
                    ):
                        raise CertificationTestFailure(
                            error.returncode, error.cmd
                        ) from error
                raise
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
            "linux-x64": ["default", "dev", "vep-rs", "fastvep"],
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
        if host in {"linux-x64", "macos-x64", "macos-arm64"}:
            commands.append(
                [*pixi, "python", "scripts/ci_certify_inputs.py", "--reference"]
            )
        if host.startswith("windows"):
            commands.append(
                [
                    "cmd",
                    "/c",
                    # .\ so cmd finds it with NoDefaultCurrentDirectoryInExePath set.
                    r".\install_windows.bat",
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
                "tests/wgsextract",
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
        [*pixi, "python", "scripts/check_python_size.py"],
        [*pixi, "actionlint"],
        [*pixi, "python", "scripts/validate_github_actions.py"],
        [*pixi, "python", "scripts/validate_site_sources.py"],
    ]
    policy = ci_test_targets(paths)
    if policy:
        commands.append([*pixi, "pytest", *policy])
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
    docs = documentation_targets(paths, targets)
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


def changed_paths(base: str, sha: str) -> list[str]:
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{sha}", "--"]
    )
    return changed.decode().rstrip("\0").split("\0") if changed else []


def selection(paths: list[str], host: str) -> tuple[bool, list[str] | str]:
    selected = routes(paths)
    os = {
        "linux-x64": "ubuntu",
        "linux-arm64": "arm64",
        "macos-arm64": "macos",
        "macos-x64": "macos",
        "windows-x64": "windows",
    }[host]
    return selected["cli"], os_selection(
        paths, select_tests(paths), cli=selected["cli"]
    )[os]


def required_environments(
    name: str, host: str, cli: bool, targets: list[str] | str
) -> list[str]:
    environments = ["default", "dev"]
    if name == "generic" or not cli or not targets:
        return environments
    environments += ["vep" if host == "linux-arm64" else "vep-rs"]
    if host == "windows-x64":
        environments.append("gatk")
    if host in {"linux-x64", "windows-x64"} and (
        targets == "all" or "tests/test_imputation.py" in targets
    ):
        environments.append("imputation")
    if host == "linux-x64" and (
        targets == "all"
        or any(
            path in targets
            for path in (
                "tests/test_ont_hg002.py",
                "tests/test_phasing.py",
                "tests/test_somalier_relate.py",
            )
        )
    ):
        environments += ["pacbio", "phasing", "somalier"]
    return environments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--platform-only", action="store_true")
    mode.add_argument("--generic-only", action="store_true")
    parser.add_argument("--remote", choices=["randy-mini"])
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    repository, pr, sha = pr_head()
    if args.remote:
        from scripts.ci_certify_remote import certify_remote

        certify_remote(repository, pr, sha, args)
        return
    host = host_platform()
    subprocess.run(["git", "fetch", "origin", pr["base"]["sha"]], check=True)
    paths = changed_paths(pr["base"]["sha"], sha)
    targets = select_tests(paths)
    cli, platform_targets = selection(paths, host)
    layers = {}
    if not args.generic_only:
        layers[host] = platform_commands(host, platform_targets, not cli)
    if not args.platform_only:
        layers["generic"] = generic_commands(paths, targets)
    summaries = []
    for name, commands in layers.items():

        def operation(
            name: str = name,
            commands: list[list[str]] = commands,
        ) -> tuple[str, str]:
            validate_environments(
                required_environments(name, host, cli, platform_targets)
            )
            summary, log = run_layer(name, commands, sha, args.workers)
            validate_head(
                gh_api(f"repos/{repository}/pulls/{pr['number']}"), sha, repository
            )
            return summary, log

        summary, log = publish_certification(repository, sha, name, operation)
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
