#!/usr/bin/env python3
"""Point the v6 row of https://wgsextract.github.io/ at a release.

Usage: python scripts/update_main_site.py README.md v0.8.6 2026-11-10

Rewrites the version and date in the "Latest Releases" table's v6 row of the
wgsextract.github.io README; the row's link to the app site stays as it is.
Never moves the row back to an older version. app_release_watch.yml runs this
once the latest release carries the app's installers.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

# | **[v6](https://wgsextract.github.io/wgsextract-cli/app/)** | 0.6.6 | 6 Nov 2026 | ... |
ROW = re.compile(
    r"^(?P<head>\|\s*\*\*\[v6\]\([^)]*\)\*\*\s*\|\s*)(?P<version>[^|]*?)"
    r"(?P<mid>\s*\|\s*)(?P<date>[^|]*?)(?P<tail>\s*\|.*)$",
    re.MULTILINE,
)


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def updated(text: str, version: str, day: date) -> str | None:
    """The README with the v6 row at this version and date, or None if the
    row already shows it or a newer version."""
    match = ROW.search(text)
    if not match:
        raise ValueError("no v6 row in the Latest Releases table")
    current = match["version"].strip()
    if re.fullmatch(r"\d+\.\d+\.\d+", current) and version_key(current) >= version_key(
        version
    ):
        return None
    row = (
        f"{match['head']}{version}{match['mid']}"
        f"{day.day} {day:%b} {day.year}{match['tail']}"
    )
    return text[: match.start()] + row + text[match.end() :]


def main(argv: list[str]) -> int:
    if len(argv) != 4 or not re.fullmatch(r"v\d+\.\d+\.\d+", argv[2]):
        print(__doc__, file=sys.stderr)
        return 2
    path, version, day = Path(argv[1]), argv[2][1:], date.fromisoformat(argv[3])
    # newline="" keeps the file's own line endings, so only the row changes.
    with path.open(encoding="utf-8", newline="") as f:
        original = f.read()
    try:
        text = updated(original, version, day)
    except ValueError as e:
        print(f"{path}: {e}; update it by hand.", file=sys.stderr)
        return 1
    if text is None:
        print(f"{path} already lists v6 at {version} or newer.")
        return 0
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(f"{path}: v6 is now {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
