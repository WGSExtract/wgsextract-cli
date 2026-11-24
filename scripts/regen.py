"""Regenerate committed CLI references; --resolve also stages merge resolutions."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = "tests/fixtures/cli_snapshot"
RETIRED_SNAPSHOT = "tests/fixtures/api_cli_snapshot.json"
GENERATOR_SCRIPTS = (
    "tests/cli_snapshot.py",
    "scripts/gen_api_docs.py",
    "scripts/gen_command_docs.py",
    "scripts/gen_config_docs.py",
    "scripts/gen_output_schemas.py",
    "scripts/gen_report_schema.py",
    "scripts/gen_server_contracts.py",
)
GENERATED = (
    "docs/api.md",
    "docs/commands.md",
    "src/wgsextract_cli/assets/cli/help-index.json",
    "docs/configuration.md",
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
    "src/wgsextract_cli/assets/schemas/wgsextract.server.openapi.json",
    "gui/app/shared/generated/server-transport.ts",
    "gui/app/shared/generated/result-records.ts",
    "gui/app/shared/generated/report-records.ts",
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
        retired = [RETIRED_SNAPSHOT] if RETIRED_SNAPSHOT in unresolved else []
        sources = {
            path
            for path in unresolved
            if path not in GENERATED
            and path not in retired
            and not (path.startswith(f"{SNAPSHOT_DIR}/") and path.endswith(".json"))
        }
        if sources:
            parser.error(
                "resolve and stage source conflicts first: "
                + ", ".join(sorted(sources))
            )
    for script in GENERATOR_SCRIPTS:
        subprocess.run([sys.executable, script], cwd=ROOT, check=True)
    if args.resolve:
        if retired:
            (ROOT / RETIRED_SNAPSHOT).unlink(missing_ok=True)
        subprocess.run(
            [
                "git",
                "add",
                "--",
                *GENERATED,
                f":(glob){SNAPSHOT_DIR}/**/*.json",
                *retired,
                "site_src/cli.md",
            ],
            cwd=ROOT,
            check=True,
        )


if __name__ == "__main__":
    main()
