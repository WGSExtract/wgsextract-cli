#!/usr/bin/env python3
"""Set the release version everywhere the release workflow checks it.

Usage: python scripts/bump_version.py 0.4.4

Updates pyproject.toml, pixi.toml and src/wgsextract_cli/__init__.py, the CLI
site's current-release text (site_src/), then points the desktop app's bundle
and its site at the same CLI release with
gui/scripts/bump-wgsextract-cli.py; release.yml compares all of them with the
vX.Y.Z tag (see docs/releasing.md).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# File, and the line that holds the version (the first match only).
VERSIONS = [
    ("pyproject.toml", r'^version = "([^"]+)"$'),
    ("pixi.toml", r'^version = "([^"]+)"$'),
    ("src/wgsextract_cli/__init__.py", r'^__version__ = "([^"]+)"$'),
]
# CLI site pages that name the current release
# (https://wgsextract.github.io/wgsextract-cli/). Every mention of the old
# version in these is replaced, so write past releases ("since v0.6.6") only as
# versions older than the current one.
SITE = ["site_src/index.md", "site_src/install.md"]
GUI_BUMP = "gui/scripts/bump-wgsextract-cli.py"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", argv[1]):
        print(__doc__, file=sys.stderr)
        return 2
    new = argv[1]
    # Prepare every change before writing any, so a failure changes nothing.
    changes: dict[Path, str] = {}
    old = set()
    for name, pattern in VERSIONS:
        path = ROOT / name
        text = path.read_text()
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            print(f"{name} has no version line; update it by hand.", file=sys.stderr)
            return 1
        old.add(match[1])
        start, end = match.span(1)
        changes[path] = text[:start] + new + text[end:]
    for current in old - {new}:
        # The version alone, not inside another (10.4.3, 0.4.30).
        version = re.compile(rf"(?<![\d.]){re.escape(current)}(?!\.?\d)")
        for name in SITE:
            path = ROOT / name
            text, count = version.subn(new, changes.get(path, path.read_text()))
            if not count:
                print(
                    f"{name} doesn't mention {current}; update it by hand.",
                    file=sys.stderr,
                )
                return 1
            changes[path] = text
    # Write each to a temporary file first and rename them all into place; if
    # anything fails, put back the files already replaced.
    originals = {path: path.read_text() for path in changes}
    staged = {path: path.with_name(path.name + ".bump") for path in changes}
    replaced: list[Path] = []
    try:
        for path, text in changes.items():
            staged[path].write_text(text)
        for path in changes:
            staged[path].replace(path)
            replaced.append(path)
    except OSError as e:
        for path in replaced:
            path.write_text(originals[path])
        for tmp in staged.values():
            tmp.unlink(missing_ok=True)
        print(
            f"Could not write the new version ({e}); nothing changed.", file=sys.stderr
        )
        return 1
    # The app's script prepares all its changes before writing any, so when it
    # fails only the files above need putting back.
    gui = subprocess.run([sys.executable, str(ROOT / GUI_BUMP), f"v{new}"])
    if gui.returncode:
        for path in changes:
            path.write_text(originals[path])
        print(f"{GUI_BUMP} failed; nothing changed.", file=sys.stderr)
        return 1
    print(f"Version {new} (was {', '.join(sorted(old))}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
