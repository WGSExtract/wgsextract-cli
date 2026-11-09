#!/usr/bin/env python3
"""Data source for the BAM page's Info Commands: whether Detailed Coverage has already run.

Prints `{"values": {"coverage_done": "true" | "false"}}`. Done means both reports
`info calculate-coverage --with-bins` writes, `<name>_coverage.tsv` and
`<name>_coverage_bins.tsv`, exist in the output folder and were written after the
BAM/CRAM last changed: the CLI then shows them again instead of rescanning.

Usage: coverage-state.py BAM_OR_CRAM [OUTPUT_DIR]   (scripts/posix, scripts/windows)
The output folder defaults to the input's folder, as the CLI's does.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def is_fresh(report: Path, source: Path) -> bool:
    try:
        return report.stat().st_size > 0 and report.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def coverage_done(input_path: str, outdir: str) -> bool:
    if not input_path:
        return False
    source = Path(input_path).expanduser()
    folder = Path(outdir).expanduser() if outdir else source.parent
    return all(is_fresh(folder / f"{source.name}{suffix}", source) for suffix in ("_coverage.tsv", "_coverage_bins.tsv"))


def main(argv: list[str]) -> int:
    input_path = argv[1].strip() if len(argv) > 1 else ""
    outdir = argv[2].strip() if len(argv) > 2 else ""
    json.dump({"values": {"coverage_done": str(coverage_done(input_path, outdir)).lower()}}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
