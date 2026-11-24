"""Measure first-use and warm compact help with isolated per-run caches."""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from wgsextract_cli.cli.help_index import PACKAGE_ROOT


def measure(command: list[str], env: dict[str, str]) -> float:
    before = time.perf_counter()
    output = subprocess.check_output(command, env=env)
    elapsed = 1000 * (time.perf_counter() - before)
    assert json.loads(output)["schema"] == "wgsextract.help-index/1"
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out/help-timings"))
    parser.add_argument("--repeats", type=int, default=15)
    args = parser.parse_args()
    source = PACKAGE_ROOT.parent.parent
    if (
        not (source / "pyproject.toml").is_file()
        or (source / "tmp" / ".managed-help-cache").is_file()
    ):
        parser.error("run this benchmark from an unmanaged source checkout")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    args.output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-m", "wgsextract_cli.main", "help", "--all", "--json"]
    cold = []
    warm = []
    for index in range(args.repeats):
        home = (args.output / str(index)).resolve()
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home / "Local"),
            "APPDATA": str(home / "Roaming"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_CONFIG_HOME": str(home / "config"),
        }
        cold.append(measure(command, env))
        warm.append(measure(command, env))
    for name, values in (("first-use", cold), ("warm", warm)):
        print(
            f"{name} median={statistics.median(values):.1f} ms; max={max(values):.1f} ms"
        )


if __name__ == "__main__":
    main()
