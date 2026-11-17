#!/usr/bin/env python3
"""Route PRs using the complete git diff, including both sides of renames."""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_moves import select_moves  # noqa: E402
from scripts.ci_select_tests import (  # noqa: E402
    SMOKE_TESTS,
    ci_path,
    ci_test_targets,
    os_selection,
    select_tests,
)
from scripts.ci_test_dependencies import (  # noqa: E402
    DOCS_ONLY_SCRIPTS,
    DOCS_TEST_MODULES,
    GENERATED_DOCS,
    ROOT,
    docs_test_targets,
    test_inputs,
)
from scripts.ci_test_tiers import SINGLE_OS_MODULES  # noqa: E402

SITE_PREFIXES = ("site/", "site_src/", "gui/site_src/", "gui/mockups/")
SITE_SCRIPTS = (
    "scripts/build_site.py",
    "scripts/validate_site_sources.py",
    "scripts/build_mockup_previews.py",
    "gui/scripts/build_site.py",
    "gui/scripts/validate_site_sources.py",
)
INSTALL_PREFIXES = (
    ".github/actions/",
    "packaging/",
    "scripts/installlab/",
    "src/wgsextract_cli/cli/",
    "gui/app/web/packagers/",
    "gui/tools/packaging/",
    "gui/examples/WGSExtract/",
)
INSTALL_FILES = {
    "pixi.toml",
    "pixi.lock",
    "pyproject.toml",
    "install.sh",
    "install_windows.bat",
    "uninstall.sh",
    "uninstall_windows.bat",
    "scripts/bootstrap_windows_prereqs.ps1",
    "src/wgsextract_cli/main.py",
    "src/wgsextract_cli/__main__.py",
    "gui/tools/tasks.py",
    ".github/workflows/install_lab.yml",
    ".github/workflows/gui-build.yml",
}


def setup_checks(paths: list[str]) -> bool:
    return any(
        not path.endswith(".md")
        and (
            path.startswith(
                (
                    "packaging/",
                    "gui/",
                    ".github/",
                    "scripts/installlab/",
                    "src/wgsextract_cli/cli/",
                )
            )
            or path in INSTALL_FILES
            or path.startswith(("scripts/test_install", "scripts/dev_setup"))
        )
        for path in paths
    )


def queue_windows_required(paths: list[str]) -> bool:
    """Code and uncertain runtime inputs require Windows, even without consumers."""
    return any(
        not path.endswith(".md")
        and not ci_path(path)
        and path not in GENERATED_DOCS
        and path not in DOCS_ONLY_SCRIPTS
        and path not in DOCS_TEST_MODULES
        for path in paths
    )


def site_validation(paths: list[str]) -> bool:
    return any(
        path.startswith(("site_src/", "gui/site_src/"))
        or path
        in {"scripts/validate_site_sources.py", "gui/scripts/validate_site_sources.py"}
        for path in paths
    )


def documentation_targets(
    paths: list[str], tests: list[str] | str, *, repository: Path = ROOT
) -> list[str]:
    targets = docs_test_targets(paths, repository=repository)
    if isinstance(tests, list):
        targets += sorted((set(tests) & DOCS_TEST_MODULES) - set(targets))
    return targets


def routes(
    paths: list[str],
    *,
    force_install_lab: bool = False,
    draft: bool = False,
    repository: Path = ROOT,
) -> dict[str, bool]:
    result = dict.fromkeys(
        ("cli", "docs", "native", "site", "install", "fast", "draft_cli"), False
    )
    result["install"] = force_install_lab
    for path in paths:
        if ci_path(path):
            continue
        if path.endswith(".md") or path in GENERATED_DOCS:
            continue
        docs_python = (
            path in DOCS_ONLY_SCRIPTS
            or (path.startswith("scripts/ci_") and path.endswith(".py"))
            or path in DOCS_TEST_MODULES
        )
        readers = set().union(
            *(
                modules
                for pattern, modules in test_inputs(repository).items()
                if path == pattern or fnmatch.fnmatchcase(path, pattern)
            )
        )
        # Non-Markdown test inputs still take precedence over area exclusions.
        uncovered = readers - DOCS_TEST_MODULES
        if path == "scripts/gen_api_docs.py":
            uncovered.discard("tests/test_api.py")
        if path == "scripts/gen_config_docs.py":
            uncovered.discard("tests/test_core_cli.py")
        if uncovered or (path.endswith(".py") and not docs_python):
            result["cli"] = True
        if (
            path.startswith(SITE_PREFIXES)
            or path in SITE_SCRIPTS
            or (path.startswith("gui/scripts/site_") and path.endswith(".py"))
        ):
            result["site"] = True
            continue
        if docs_python:
            continue
        if path.startswith("native/"):
            result["native"] = True
            continue
        if path.startswith(INSTALL_PREFIXES) or path in INSTALL_FILES:
            result["install"] = True
        if path.startswith("gui/"):
            continue
        if path.startswith(".github/workflows/gui-"):
            continue
        # Unknown paths deliberately fail open to the full CLI matrix.
        result["cli"] = True
    tests = select_tests(paths, repository=repository)
    covered = set(ci_test_targets(paths, repository=repository)) | {
        target.split("::")[0]
        for target in documentation_targets(paths, tests, repository=repository)
    }
    result["cli"] = (
        tests == "all" or bool(tests) and (result["cli"] or bool(set(tests) - covered))
    )
    if draft:
        result["fast"] = bool(paths) and any(
            result[area] for area in ("cli", "native", "site", "install")
        )
        result["draft_cli"] = result["cli"]
        for area in ("cli", "native", "site", "install"):
            result[area] = False
    result["docs"] = bool(
        documentation_targets(paths, tests, repository=repository)
        if not result["cli"]
        else []
    ) or (site_validation(paths) and not result["site"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--force-install-lab", action="store_true")
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--full-suite", action="store_true")
    parser.add_argument(
        "--print-tests",
        action="store_true",
        help="Print only JSON test files or all for the diff.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    changed = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            f"{args.base}...{args.head}",
            "--",
        ]
    )
    paths = changed.decode().rstrip("\0").split("\0") if changed else []
    selected = routes(paths, force_install_lab=args.force_install_lab, draft=args.draft)
    queue_windows = queue_windows_required(paths)
    tests = "all" if args.full_suite else select_tests(paths)
    moves = None if args.full_suite else select_moves(args.base, args.head)
    if moves is not None:
        tests = moves.tests
    if args.full_suite:
        selected["cli"] = True
    if args.print_tests:
        print(tests if isinstance(tests, str) else json.dumps(tests))
        return
    ci_tests = ci_test_targets(paths)
    output = "".join(f"{key}={str(value).lower()}\n" for key, value in selected.items())
    output += (
        f"setup_checks={str(setup_checks(paths) or args.force_install_lab).lower()}\n"
    )
    output += "move_modules=" + json.dumps(moves.modules if moves else []) + "\n"
    output += (
        "docs_tests="
        + json.dumps(
            [
                target
                for target in (
                    documentation_targets(paths, tests) if not selected["cli"] else []
                )
                if target.split("::")[0] not in ci_tests
            ]
        )
        + "\n"
        + f"site_validation={str(site_validation(paths) and not selected['site']).lower()}\n"
    )
    output += "tests=" + (tests if isinstance(tests, str) else json.dumps(tests)) + "\n"
    output += (
        "generic_tests="
        + json.dumps(
            [
                target
                for target in (
                    sorted(path.as_posix() for path in Path("tests").glob("test_*.py"))
                    if tests == "all"
                    else tests
                )
                if Path(target).name in SINGLE_OS_MODULES
            ]
        )
        + "\n"
    )
    for os, targets in os_selection(paths, tests, cli=selected["cli"]).items():
        if moves is not None and targets == []:
            # Import/collection validation still needs a runner when no test imports it.
            targets = list(SMOKE_TESTS)
        if args.full_suite and os == "windows" and not queue_windows:
            targets = []
        output += (
            f"{os}_tests="
            + (targets if isinstance(targets, str) else json.dumps(targets))
            + "\n"
        )
        output += f"{os}_smoke={str(not selected['cli'] and bool(targets)).lower()}\n"
    output += f"ci={str(any(ci_path(path) for path in paths)).lower()}\n"
    output += "ci_tests=" + json.dumps(ci_tests) + "\n"
    output += (
        "python_files="
        + json.dumps(
            [path for path in paths if path.endswith(".py") and Path(path).is_file()]
        )
        + "\n"
    )
    output += "routed=true\n"
    print(output, end="")
    if args.output:
        with args.output.open("a", encoding="utf-8") as stream:
            stream.write(output)


if __name__ == "__main__":
    main()
