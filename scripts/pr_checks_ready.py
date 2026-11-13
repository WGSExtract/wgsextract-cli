#!/usr/bin/env python3
"""Require a passing non-draft area run, not stale draft checks, before landing."""

from __future__ import annotations

import json
import sys
from typing import Any


def checks_ready(checks: list[dict[str, Any]]) -> bool:
    if not checks or any(
        check.get("bucket") not in {"pass", "skipping"} for check in checks
    ):
        return False
    for gate in checks:
        if (
            gate.get("name") != "Ready PR area checks"
            or gate.get("workflow") != "PR area checks"
            or gate.get("bucket") != "pass"
        ):
            continue
        run, separator, _job = str(gate.get("link", "")).rpartition("/job/")
        if not separator or "/actions/runs/" not in run:
            continue
        run_checks = [
            check
            for check in checks
            if check.get("workflow") == "PR area checks"
            and str(check.get("link", "")).startswith(f"{run}/job/")
        ]
        if any(
            check.get("name") == "changes" and check.get("bucket") == "pass"
            for check in run_checks
        ) and all(
            check.get("bucket") == "skipping"
            for check in run_checks
            if check.get("name") == "Draft lint and fast tests"
        ):
            return True
    return False


def main() -> int:
    checks = json.load(sys.stdin)
    if not isinstance(checks, list) or not checks_ready(checks):
        print(
            "Checks are incomplete/failing or lack a passing non-draft area run.",
            file=sys.stderr,
        )
        return 1
    print("Non-draft area validation and all reported checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
