"""Versioned shared review store; connections belong to individual requests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class ReviewDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"unsupported notes schema: {version}")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS pages ("
                "idea TEXT NOT NULL, page TEXT NOT NULL, doc TEXT NOT NULL, "
                "PRIMARY KEY (idea, page))"
            )
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def load(self, idea: str, page: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT doc FROM pages WHERE idea=? AND page=?", (idea, page)
            ).fetchone()
        return (
            json.loads(row[0])
            if row
            else {"idea": idea, "page": page, "title": "", "notes": {}}
        )

    def save(self, idea: str, page: str, doc: dict[str, Any]) -> None:
        with self.connect() as db:
            self.write(db, idea, page, doc)

    def write(
        self, db: sqlite3.Connection, idea: str, page: str, doc: dict[str, Any]
    ) -> None:
        db.execute(
            "INSERT INTO pages VALUES (?, ?, ?) "
            "ON CONFLICT(idea, page) DO UPDATE SET doc=excluded.doc",
            (idea, page, json.dumps({**doc, "idea": idea, "page": page})),
        )

    def documents(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT doc FROM pages ORDER BY idea, page")
            ]
