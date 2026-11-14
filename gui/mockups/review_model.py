"""Validated page-note operations shared by file and database storage."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from review_access import BadRequest

ITEM_ID = re.compile(r"^[a-z][a-z0-9-]{3,40}$")
STATUSES = ("open", "resolved")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def text_field(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"{name} must be non-empty text")
    if len(value) > limit:
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"{name} is longer than {limit}")
    return value.strip()


def item_id(value: Any) -> str:
    if not isinstance(value, str) or not ITEM_ID.fullmatch(value):
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"bad id: {value!r}")
    return value


def clean_reply(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "reply must be an object")
    reply = {
        "author": text_field(raw.get("author"), "author", 40),
        "id": item_id(raw.get("id")),
        "text": text_field(raw.get("text"), "text", 8000),
    }
    if isinstance(raw.get("created"), str):
        reply["created"] = raw["created"]
    return reply


def clean_note(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "note must be an object")
    replies = raw.get("replies", [])
    if not isinstance(replies, list):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "replies must be a list")
    note: dict[str, Any] = {
        "anchor": raw.get("anchor") if isinstance(raw.get("anchor"), dict) else None,
        "author": text_field(raw.get("author"), "author", 40),
        "context": raw.get("context") if isinstance(raw.get("context"), dict) else {},
        "id": item_id(raw.get("id")),
        "replies": [clean_reply(r) for r in replies],
        "status": raw.get("status", "open"),
        "text": text_field(raw.get("text"), "text", 8000),
    }
    if note["status"] not in STATUSES:
        raise BadRequest(HTTPStatus.BAD_REQUEST, "status must be open or resolved")
    for stamp in ("created", "updated"):
        if isinstance(raw.get(stamp), str):
            note[stamp] = raw[stamp]
    return note


def apply_op(doc: dict[str, Any], change: Any) -> dict[str, Any]:
    """Apply one change to a page's notes (mirrors applyOp in store.js)."""
    if not isinstance(change, dict):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
    notes: dict[str, Any] = doc["notes"]
    op = change.get("op")
    stamp = now()
    if op == "add":
        note = clean_note(change.get("note"))
        if note["id"] in notes:
            raise BadRequest(HTTPStatus.CONFLICT, "a note with this id exists")
        note.update(created=stamp, updated=stamp, status="open", replies=[])
        notes[note["id"]] = note
    elif op == "merge":
        incoming = change.get("notes")
        if not isinstance(incoming, dict):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "notes must be an object")
        for raw in incoming.values():
            note = clean_note(raw)
            mine: dict[str, Any] | None = notes.get(note["id"])
            if mine is None or str(note.get("updated", "")) > str(
                mine.get("updated", "")
            ):
                notes[note["id"]] = note
    elif op not in ("reply", "status", "edit", "delete"):
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"unknown op: {op!r}")
    else:
        note_id = item_id(change.get("id"))
        found: dict[str, Any] | None = notes.get(note_id)
        if found is None:
            raise BadRequest(HTTPStatus.NOT_FOUND, f"no note {note_id}")
        note = found
        if op == "reply":
            reply = clean_reply(change.get("reply"))
            reply["created"] = stamp
            note["replies"].append(reply)
        elif op == "status":
            if change.get("status") not in STATUSES:
                raise BadRequest(
                    HTTPStatus.BAD_REQUEST, "status must be open or resolved"
                )
            note["status"] = change["status"]
        elif op == "edit":
            note["text"] = text_field(change.get("text"), "text", 8000)
        else:
            del notes[note_id]
            return doc
        note["updated"] = stamp
    return doc
