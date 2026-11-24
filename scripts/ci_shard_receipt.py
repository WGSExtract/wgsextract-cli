"""Reassemble an advisory Linux selection receipt from its pytest jobs."""

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.ci_test_shards import Shard


def combine(shards: list[Shard], reports: Path, output: Path) -> None:
    suites = ET.Element("testsuites")
    selected: set[str] = set()
    modes = {shard["gate"] for shard in shards}
    if len(modes) != 1 or "" in modes:
        raise ValueError("Linux pytest shards must share one nonempty selector mode")
    (mode,) = modes
    outcome = "success"
    for shard in shards:
        label = shard["label"]
        if not re.fullmatch(r"all|shard-[23]", label):
            raise ValueError("Invalid Linux shard matrix")
        paths = set(json.loads(shard["selection"]))
        if selected & paths:
            raise ValueError("Linux pytest shards overlap")
        selected.update(paths)
        directory = reports / f"tests-ubuntu-{label}"
        status = int((directory / "shard-exit-status").read_text(encoding="utf-8"))
        if status != 0:
            outcome = "failure"
        junit = directory / "pytest.xml"
        if junit.is_file():
            suites.extend(ET.parse(junit).iter("testsuite"))
    output.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suites).write(output / "pytest.xml", encoding="utf-8")
    (output / "gate.json").write_text(
        json.dumps({"mode": mode, "selection": sorted(selected)}) + "\n",
        encoding="utf-8",
    )
    (output / "outcome").write_text(outcome + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=json.loads, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    combine(args.matrix, args.reports, args.output)


if __name__ == "__main__":
    main()
