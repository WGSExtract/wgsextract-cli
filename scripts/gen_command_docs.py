#!/usr/bin/env python3
"""Write the compact CLI discovery index
from the command tiers, journeys, deprecations and the help catalog.

Run with --check to fail (exit 1) when any generated file is out of date.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wgsextract_cli.cli.help import (
    command_index,
    global_options_json,
    index_json,
)
from wgsextract_cli.cli.parser import build_parser
from wgsextract_cli.core.config import settings_suspended

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src/wgsextract_cli/assets/cli/help-index.json"


def main() -> int:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--check", action="store_true", help="only check")
    args = options.parse_args()
    with settings_suspended():
        parser = build_parser()[0]
        wanted = {
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
