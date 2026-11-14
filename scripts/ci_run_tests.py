"""Pass a validated PR selection to test-ci without shell interpolation."""

from __future__ import annotations

import argparse
import fnmatch
import json
import shlex
import subprocess
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


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
    manifest = Path(__file__).resolve().parents[1] / "pixi.toml"
    task = tomllib.loads(manifest.read_text(encoding="utf-8"))["feature"]["dev"][
        "tasks"
    ]["test-ci"]
    words = shlex.split(task if isinstance(task, str) else task["cmd"])
    ignored: set[str] = set()
    patterns: set[str] = set()
    for index, word in enumerate(words):
        option, _, value = word.partition("=")
        if option not in {"--ignore", "--ignore-glob"}:
            continue
        value = value or words[index + 1]
        (ignored if option == "--ignore" else patterns).add(value.rstrip("/"))
    return [
        target
        for target in targets
        if not any(target == path or target.startswith(path + "/") for path in ignored)
        and not any(fnmatch.fnmatchcase(target, pattern) for pattern in patterns)
    ]


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
