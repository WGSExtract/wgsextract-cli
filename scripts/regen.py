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
    "src/wgsextract_cli/assets/cli/help-index.json",
    "docs/configuration.md",
    "src/wgsextract_cli/assets/schemas/wgsextract.sample.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.steps.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.result.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.report.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.server.responses.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.server.openapi.json",
    "gui/app/shared/generated/server-transport.ts",
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
        "scripts/gen_config_docs.py",
        "scripts/gen_output_schemas.py",
        "scripts/gen_report_schema.py",
        "scripts/gen_server_contracts.py",
    ):
        subprocess.run([sys.executable, script], cwd=ROOT, check=True)
    if args.resolve:
        subprocess.run(
            ["git", "add", "--", *GENERATED, "site_src/cli.md"], cwd=ROOT, check=True
        )


if __name__ == "__main__":
    main()
