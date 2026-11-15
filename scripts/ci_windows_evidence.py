"""Select Windows evidence while an upstream app job is already failing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def expected_evidence(root: Path, names: list[str], app_result: str) -> list[str]:
    selected = []
    for name in names:
        results = root / name
        if (results / "results").is_dir():
            results /= "results"
        if (
            app_result in {"failure", "cancelled"}
            and name.startswith("install-lab-windows-app-")
            and not (results / "lab-run.json").is_file()
        ):
            print(
                f"::notice::ci-evidence-skipped: {name}: windows-app {app_result}",
                file=sys.stderr,
            )
            continue
        selected.append(name)
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--app-job-result",
        choices=["success", "failure", "cancelled", "skipped"],
        required=True,
    )
    parser.add_argument("names", nargs="+")
    args = parser.parse_args(argv)
    print(" ".join(expected_evidence(args.root, args.names, args.app_job_result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
