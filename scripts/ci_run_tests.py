"""Pass a validated PR selection to test-ci without shell interpolation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_test_tiers import SINGLE_OS_MODULES  # noqa: E402

SEPARATE_SUITES = {
    "test_e2e_fast_chrM.py",
    "test_e2e_full_genome.py",
    "test_microarray_real_data.py",
    "test_smoke_real_full.py",
}


def test_targets(selection: str) -> list[str] | None:
    if selection == "all":
        return None
    targets = json.loads(selection)
    if not isinstance(targets, list) or not all(
        isinstance(target, str)
        and target.startswith("tests/")
        and target.endswith(".py")
        and ".." not in Path(target).parts
        and Path(target).is_file()
        for target in targets
    ):
        raise ValueError("Selection must contain existing repository test files")
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", default="all")
    parser.add_argument("--generic-only", action="store_true")
    parser.add_argument("--platform-only", action="store_true")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    targets = test_targets(args.selection)
    if args.generic_only:
        targets = [
            target
            for target in (
                targets
                if targets is not None
                else sorted(path.as_posix() for path in Path("tests").glob("test_*.py"))
            )
            if Path(target).name in SINGLE_OS_MODULES
        ]
    if targets is not None:
        targets = [
            target for target in targets if Path(target).name not in SEPARATE_SUITES
        ]
    if targets == []:
        print("No affected tests; skipping pytest.")
        return
    options = args.pytest_args
    if options[:1] == ["--"]:
        options = options[1:]
    if args.platform_only:
        options.append("--ci-skip-independent")
    result = subprocess.run(["pixi", "run", "test-ci", *options, *(targets or [])])
    # A sparse selection may contain no tests in this tier/hash shard.
    if result.returncode == 5 and targets is not None:
        print("No affected tests in this CI partition.")
        return
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
