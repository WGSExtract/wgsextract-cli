#!/usr/bin/env python3
"""Route PRs using the complete git diff, including both sides of renames."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

SITE_PREFIXES = ("site/", "site_src/", "gui/site_src/", "gui/mockups/")
SITE_SCRIPTS = (
    "scripts/build_site.py",
    "scripts/validate_site_sources.py",
    "scripts/build_mockup_previews.py",
    "gui/scripts/build_site.py",
    "gui/scripts/validate_site_sources.py",
)
SHARED_GUI_FILES = {
    "gui/scripts/bump-wgsextract-cli.py",
    "gui/examples/WGSExtract/scripts/wgsextract-release-tag.txt",
    "gui/examples/WGSExtract/manifest.json",
    "gui/app/tests/conformance.test.mjs",
    "gui/tools/packaging/tests/test_embedded_branding.py",
}
INSTALL_PREFIXES = (
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


def routes(paths: list[str], *, force_install_lab: bool = False) -> dict[str, bool]:
    result = dict.fromkeys(("cli", "docs", "native", "site", "install"), False)
    result["install"] = force_install_lab
    for path in paths:
        # Site Markdown belongs to Pages, not the generated CLI documentation.
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
        if path.startswith("gui/") and path not in SHARED_GUI_FILES:
            continue
        if path.startswith(".github/workflows/gui-"):
            continue
        # Unknown paths deliberately fail open to the full CLI matrix.
        result["cli"] = True
        result["docs"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--force-install-lab", action="store_true")
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
    selected = routes(paths, force_install_lab=args.force_install_lab)
    output = "".join(f"{key}={str(value).lower()}\n" for key, value in selected.items())
    print(output, end="")
    if args.output:
        with args.output.open("a", encoding="utf-8") as stream:
            stream.write(output)


if __name__ == "__main__":
    main()
