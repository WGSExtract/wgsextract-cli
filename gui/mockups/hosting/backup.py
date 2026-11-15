#!/usr/bin/env python3
"""Online SQLite backup, retaining the newest fourteen dated backups."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


def backup(root: Path) -> Path:
    folder = root / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("notes-" + datetime.now().strftime("%Y%m%d") + ".db")
    staging = target.with_suffix(".partial")
    source = sqlite3.connect("file:" + str(root / "notes.db") + "?mode=ro", uri=True)
    dest = sqlite3.connect(str(staging))
    try:
        source.backup(dest)
        if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup failed integrity check")
    finally:
        dest.close()
        source.close()
    staging.replace(target)
    for old in sorted(folder.glob("notes-????????.db"), reverse=True)[14:]:
        old.unlink()
    print(target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/Users/wgse/mockup-server"))
    backup(parser.parse_args().root)
