#!/usr/bin/env python3
"""Check two PharmCAT phenotype.json files agree except for their timestamp.

python scripts/compare_pharmcat_phenotypes.py linux.phenotype.json windows.phenotype.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load(path: str) -> Any:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    document.get("matcherMetadata", {}).pop("timestamp", None)
    return document


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    first, second = (_load(path) for path in sys.argv[1:])
    if first == second:
        genes = len(first.get("geneReports", {}))
        print(f"phenotype.json files match ({genes} gene reports).")
        return 0
    for key in sorted(set(first) | set(second)):
        if first.get(key) != second.get(key):
            print(f"Differs: {key}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
