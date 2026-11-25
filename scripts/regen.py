"""Regenerate committed CLI references; --resolve also stages merge resolutions."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_COMMANDS = (
    ("scripts/gen_command_docs.py",),
    ("scripts/gen_output_schemas.py",),
    ("scripts/gen_report_schema.py",),
    ("scripts/gen_server_contracts.py",),
    ("scripts/gen_server_openapi.py", "--gui"),
)
GENERATED = (
    "src/wgsextract_cli/assets/cli/help-index.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.sample.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.steps.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.result.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.roh.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.recheck.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.delta.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.methylation-regions-hp.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.paraphase.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.panel.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.report.1.schema.json",
    "src/wgsextract_cli/assets/schemas/wgsextract.server.responses.1.schema.json",
    "gui/app/shared/generated/result-records.ts",
    "gui/app/shared/generated/report-records.ts",
)
RETIRED_GENERATED = (
    "docs/api.md",
    "docs/commands.md",
    "docs/configuration.md",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolve", action="store_true")
    args = parser.parse_args()
    retired: list[str] = []
    if args.resolve:
        unresolved = subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=U"], cwd=ROOT, text=True
        ).splitlines()
        retired = sorted(set(unresolved).intersection(RETIRED_GENERATED))
        sources = {
            path for path in unresolved if path not in GENERATED and path not in retired
        }
        if sources:
            parser.error(
                "resolve and stage source conflicts first: "
                + ", ".join(sorted(sources))
            )
    for command in GENERATOR_COMMANDS:
        subprocess.run([sys.executable, *command], cwd=ROOT, check=True)
    if args.resolve:
        for path in retired:
            (ROOT / path).unlink(missing_ok=True)
        subprocess.run(
            [
                "git",
                "add",
                "--",
                *GENERATED,
                *retired,
            ],
            cwd=ROOT,
            check=True,
        )


if __name__ == "__main__":
    main()
