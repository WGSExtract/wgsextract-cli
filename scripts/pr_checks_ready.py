#!/usr/bin/env python3
"""Exit successfully only for a nonempty, finished, passing gh pr checks report."""

from __future__ import annotations

import json
import sys
from typing import Any


def checks_ready(checks: list[dict[str, Any]]) -> bool:
    return (
        bool(checks)
        and any(check.get("bucket") == "pass" for check in checks)
        and all(check.get("bucket") in {"pass", "skipping"} for check in checks)
    )


def main() -> int:
    checks = json.load(sys.stdin)
    if not isinstance(checks, list) or not checks_ready(checks):
        print("Checks are absent, pending, cancelled or failing.", file=sys.stderr)
        return 1
    print("All reported checks passed or were intentionally skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
