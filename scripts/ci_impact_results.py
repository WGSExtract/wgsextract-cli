"""Summarize file-level pytest evidence without treating skips as passes."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import TypedDict


class Result(TypedDict):
    passed: int
    failed: int
    skipped: int
    seconds: float


def file_results(junit: Path, tests: set[str]) -> tuple[dict[str, Result], int]:
    names = {p.removesuffix(".py").replace("/", "."): p for p in tests}
    results: dict[str, Result] = defaultdict(
        lambda: {"passed": 0, "failed": 0, "skipped": 0, "seconds": 0.0}
    )
    unmapped = 0
    for case in ET.parse(junit).iter("testcase"):
        name = case.get("classname", "")
        error = case.find("error")
        if (
            not name
            and error is not None
            and error.get("message") == "collection failure"
        ):
            name = case.get("name", "")
        file = case.get("file")
        if file not in tests:
            file = None
            if ".tests." in name:
                name = "tests." + name.split(".tests.", 1)[1]
            while name and file is None:
                file = names.get(name)
                name = name.rpartition(".")[0]
        if file is None:
            unmapped += 1
            continue
        result = results[file]
        if case.find("failure") is not None or case.find("error") is not None:
            result["failed"] += 1
        elif case.find("skipped") is not None:
            result["skipped"] += 1
        else:
            result["passed"] += 1
        result["seconds"] += float(case.get("time", "0"))
    return dict(results), unmapped
