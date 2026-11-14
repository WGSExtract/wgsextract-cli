"""Pass a validated PR selection to test-ci without shell interpolation."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


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
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    targets = test_targets(args.selection)
    if targets == []:
        print("No affected tests; skipping pytest.")
        return
    options = args.pytest_args
    if options[:1] == ["--"]:
        options = options[1:]
    result = subprocess.run(["pixi", "run", "test-ci", *options, *(targets or [])])
    # A sparse selection may contain no tests in this tier/hash shard.
    if result.returncode == 5 and targets is not None:
        print("No affected tests in this CI partition.")
        return
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
