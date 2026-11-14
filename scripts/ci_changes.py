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

from scripts.ci_test_dependencies import DOCS_TEST_MODULES, ROOT, test_inputs  # noqa: E402

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
        readers = set().union(
            *(
                modules
                for pattern, modules in test_inputs(repository).items()
                if path == pattern or fnmatch.fnmatchcase(path, pattern)
            )
        )
        # Test inputs must be considered before broad site/prose/native skips.
        if readers:
            result["docs"] = True
            if not readers <= DOCS_TEST_MODULES:
                result["cli"] = True
        if path.endswith(".py"):
            result["cli"] = True
            result["docs"] = True
        if (
            path.startswith(SITE_PREFIXES)
            or path in SITE_SCRIPTS
            or (path.startswith("gui/scripts/site_") and path.endswith(".py"))
        ):
            result["site"] = True
            continue
        if path.endswith(".md"):
            result["docs"] = True
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
        result["docs"] = True
    if draft:
        result["fast"] = bool(paths)
        result["draft_cli"] = result["cli"]
        for area in ("cli", "native", "site", "install"):
            result[area] = False
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
