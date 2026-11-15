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

from scripts.ci_test_dependencies import (  # noqa: E402
    DOCS_ONLY_SCRIPTS,
    DOCS_TEST_MODULES,
    GENERATED_DOCS,
    ROOT,
    docs_test_targets,
    test_inputs,
)

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


def site_validation(paths: list[str]) -> bool:
    return any(
        path.startswith(("site_src/", "gui/site_src/"))
        or path
        in {"scripts/validate_site_sources.py", "gui/scripts/validate_site_sources.py"}
        for path in paths
    )


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
    if draft:
        result["fast"] = bool(paths) and any(
            result[area] for area in ("cli", "native", "site", "install")
        )
        result["draft_cli"] = result["cli"]
        for area in ("cli", "native", "site", "install"):
            result[area] = False
    result["docs"] = bool(
        docs_test_targets(paths, cli=result["cli"], repository=repository)
    ) or (site_validation(paths) and not result["site"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--force-install-lab", action="store_true")
    parser.add_argument("--label-event", action="store_true")
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    changed = (
        b""
        if args.label_event
        else subprocess.check_output(
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
    )
    paths = changed.decode().rstrip("\0").split("\0") if changed else []
    selected = routes(paths, force_install_lab=args.force_install_lab, draft=args.draft)
    output = "".join(f"{key}={str(value).lower()}\n" for key, value in selected.items())
    output += (
        "docs_tests="
        + json.dumps(docs_test_targets(paths, cli=selected["cli"]))
        + "\n"
        + f"site_validation={str(site_validation(paths) and not selected['site']).lower()}\n"
    )
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
