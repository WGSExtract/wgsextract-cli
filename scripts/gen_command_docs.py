#!/usr/bin/env python3
"""Write the command docs, site command cards and compact CLI discovery index
from the command tiers, journeys, deprecations and the help catalog.

Run with --check to fail (exit 1) when any generated file is out of date.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wgsextract_cli.cli.help import (
    SITE_BLOCK_END,
    SITE_BLOCK_START,
    command_index,
    global_options_json,
    index_json,
    render_markdown,
    render_site_commands,
)
from wgsextract_cli.cli.parser import build_parser
from wgsextract_cli.core.config import settings_suspended

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "commands.md"
SITE_PAGE = ROOT / "site_src" / "cli.md"
INDEX = ROOT / "src/wgsextract_cli/assets/cli/help-index.json"


def site_page_text(current: str) -> str:
    """current with its generated block replaced."""
    start = current.index(SITE_BLOCK_START)
    end = current.index(SITE_BLOCK_END) + len(SITE_BLOCK_END) + 1
    return current[:start] + render_site_commands() + current[end:]


def main() -> int:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--check", action="store_true", help="only check")
    args = options.parse_args()
    with settings_suspended():
        parser = build_parser()[0]
        wanted = {
            DOC: render_markdown(parser),
            SITE_PAGE: site_page_text(SITE_PAGE.read_text(encoding="utf-8")),
            INDEX: index_json(
                command_index(parser), global_options_json(parser), compact=True
            ),
        }
    stale = [
        path
        for path, text in wanted.items()
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]
    if args.check:
        for path in stale:
            print(f"{path} is stale; run 'pixi run gen-command-docs'.", file=sys.stderr)
        return 1 if stale else 0
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wanted[path], encoding="utf-8", newline="\n")
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
