"""Regenerate committed CLI references; --resolve also stages merge resolutions."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = (
    "tests/fixtures/api_cli_snapshot.json",
    "docs/api.md",
    "docs/commands.md",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolve", action="store_true")
    args = parser.parse_args()
    if args.resolve:
        unresolved = subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=U"], cwd=ROOT, text=True
        ).splitlines()
        sources = set(unresolved) - set(GENERATED)
        if sources:
            parser.error(
                "resolve and stage source conflicts first: "
                + ", ".join(sorted(sources))
            )
    for script in (
        "tests/cli_snapshot.py",
        "scripts/gen_api_docs.py",
        "scripts/gen_command_docs.py",
    ):
        subprocess.run([sys.executable, script], cwd=ROOT, check=True)
    if args.resolve:
        subprocess.run(
            ["git", "add", "--", *GENERATED, "site_src/cli.md"], cwd=ROOT, check=True
        )


if __name__ == "__main__":
    main()
