#!/usr/bin/env python3
"""Serve the WGS Extract UI mockups and save review notes into the repo.

    python3 gui/mockups/serve.py            # http://127.0.0.1:8765/
    python3 gui/mockups/serve.py --open     # and open it in the browser
    python3 gui/mockups/serve.py --check    # validate and tidy feedback/*.json

Static files come from this folder. The annotation tool reads and writes
feedback/<page-id>.json through a small JSON API, one file per page,
pretty-printed with sorted keys so git diffs and merges stay readable:

    GET  /api/health              {"ok": true}
    GET  /api/feedback            open/resolved counts for every page
    GET  /api/feedback/<page-id>  the page's notes
    POST /api/feedback/<page-id>  apply one change and return the notes:
         {"op": "add", "note": {...}}            {"op": "reply", "id", "reply": {...}}
         {"op": "status", "id", "status"}        {"op": "edit", "id", "text"}
         {"op": "delete", "id"}                  {"op": "merge", "notes": {...}}

Python standard library only. Listens on 127.0.0.1 unless --host says
otherwise, and then anyone who can reach the port can read and write notes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
FEEDBACK = HERE / "feedback"
PAGE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ITEM_ID = re.compile(r"^[a-z][a-z0-9-]{3,40}$")
MAX_BODY = 512 * 1024
MAX_TEXT = 8000
MAX_AUTHOR = 40
STATUSES = ("open", "resolved")
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
WRITE_LOCK = threading.Lock()


class BadRequest(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def now() -> str:
    # timezone.utc, not datetime.UTC: this runs on any python3, including 3.9
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


def feedback_path(page: str) -> Path:
    """feedback/<page>.json; the id pattern already rules out separators, and
    the containment check makes sure nothing resolves outside feedback/."""
    if not PAGE_ID.match(page):
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"bad page id: {page!r}")
    base = os.path.realpath(FEEDBACK)
    full = os.path.realpath(os.path.join(base, page + ".json"))
    if not full.startswith(base + os.sep):
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"bad page id: {page!r}")
    return Path(full)


def load(page: str) -> dict[str, Any]:
    path = feedback_path(page)
    if not path.exists():
        return {"notes": {}, "page": page, "title": ""}
    try:
        doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise BadRequest(
            HTTPStatus.CONFLICT,
            f"feedback/{page}.json isn't valid JSON ({err}); fix it, e.g. after a git merge, "
            "and run serve.py --check",
        ) from err
    if not isinstance(doc, dict) or not isinstance(doc.get("notes", {}), dict):
        raise BadRequest(HTTPStatus.CONFLICT, f"feedback/{page}.json has no notes object")
    doc.setdefault("notes", {})
    doc["page"] = page
    return doc


def dump(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def save(page: str, doc: dict[str, Any]) -> None:
    path = feedback_path(page)
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(dump(doc))
    os.replace(tmp, path)


def text_field(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"{name} must be non-empty text")
    if len(value) > limit:
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"{name} is longer than {limit}")
    return value.strip()


def item_id(value: Any) -> str:
    if not isinstance(value, str) or not ITEM_ID.match(value):
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"bad id: {value!r}")
    return value


def clean_note(raw: Any) -> dict[str, Any]:
    """The fields of a note we keep, checked; anything else is dropped."""
    if not isinstance(raw, dict):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "note must be an object")
    note: dict[str, Any] = {
        "anchor": raw.get("anchor") if isinstance(raw.get("anchor"), dict) else None,
        "author": text_field(raw.get("author"), "author", MAX_AUTHOR),
        "context": raw.get("context") if isinstance(raw.get("context"), dict) else {},
        "id": item_id(raw.get("id")),
        "replies": [clean_reply(r) for r in replies_list(raw.get("replies", []))],
        "status": raw.get("status", "open"),
        "text": text_field(raw.get("text"), "text", MAX_TEXT),
    }
    if note["status"] not in STATUSES:
        raise BadRequest(HTTPStatus.BAD_REQUEST, "status must be open or resolved")
    for stamp in ("created", "updated"):
        if isinstance(raw.get(stamp), str):
            note[stamp] = raw[stamp]
    return note


def replies_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "replies must be a list")
    return value


def clean_reply(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "reply must be an object")
    reply = {
        "author": text_field(raw.get("author"), "author", MAX_AUTHOR),
        "id": item_id(raw.get("id")),
        "text": text_field(raw.get("text"), "text", MAX_TEXT),
    }
    if isinstance(raw.get("created"), str):
        reply["created"] = raw["created"]
    return reply


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
            if mine is None or str(note.get("updated", "")) > str(mine.get("updated", "")):
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
                raise BadRequest(HTTPStatus.BAD_REQUEST, "status must be open or resolved")
            note["status"] = change["status"]
        elif op == "edit":
            note["text"] = text_field(change.get("text"), "text", MAX_TEXT)
        else:
            del notes[note_id]
            return doc
        note["updated"] = stamp
    return doc


def summary() -> dict[str, Any]:
    pages = []
    for path in sorted(FEEDBACK.glob("*.json")):
        try:
            doc = load(path.stem)
        except BadRequest as err:
            pages.append({"error": str(err), "open": 0, "page": path.stem, "resolved": 0})
            continue
        notes = list(doc["notes"].values())
        resolved = sum(1 for n in notes if n.get("status") == "resolved")
        pages.append(
            {
                "open": len(notes) - resolved,
                "page": path.stem,
                "resolved": resolved,
                "title": doc.get("title", ""),
                "updated": max((n.get("updated", "") for n in notes), default=""),
            }
        )
    return {"pages": pages}


class Handler(SimpleHTTPRequestHandler):
    server_version = "WGSEMockups/1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(HERE), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        if self.path.startswith("/api/"):
            super().log_message(format, *args)

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self.api("GET")
        else:
            super().do_GET()

    def do_POST(self) -> None:
        self.api("POST")

    def check_origin(self) -> None:
        """Refuse other sites (and DNS rebinding) when serving on loopback."""
        address = self.server.server_address
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            return
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in LOOPBACK:
            raise BadRequest(HTTPStatus.FORBIDDEN, "bad Host header")
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise BadRequest(HTTPStatus.FORBIDDEN, "cross-site request refused")

    def read_json(self) -> Any:
        if (self.headers.get("Content-Type") or "").split(";")[0] != "application/json":
            raise BadRequest(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY:
            raise BadRequest(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body missing or too large")
        try:
            return json.loads(self.rfile.read(length))
        except ValueError as err:  # also covers JSONDecodeError and bad UTF-8
            raise BadRequest(HTTPStatus.BAD_REQUEST, f"bad JSON: {err}") from err

    def api(self, method: str) -> None:
        try:
            self.check_origin()
            route = urlsplit(self.path).path.rstrip("/").split("/")[2:]
            if method == "GET" and route == ["health"]:
                self.reply({"ok": True, "writable": os.access(FEEDBACK, os.W_OK)})
            elif method == "GET" and route == ["feedback"]:
                self.reply(summary())
            elif len(route) == 2 and route[0] == "feedback" and method == "GET":
                self.reply(load(route[1]))
            elif len(route) == 2 and route[0] == "feedback" and method == "POST":
                change = self.read_json()
                with WRITE_LOCK:
                    doc = apply_op(load(route[1]), change)
                    if isinstance(change, dict) and isinstance(change.get("title"), str):
                        doc["title"] = change["title"][:200]
                    save(route[1], doc)
                self.reply(doc)
            else:
                raise BadRequest(HTTPStatus.NOT_FOUND, "no such endpoint")
        except BadRequest as err:
            self.reply({"error": {"message": str(err)}}, err.status)
        # A bug, not a bad request: answer rather than drop the connection.
        except Exception as err:  # noqa: BLE001
            self.log_error("notes API error: %r", err)
            self.reply(
                {"error": {"message": f"server error: {err}"}}, HTTPStatus.INTERNAL_SERVER_ERROR
            )

    def reply(self, body: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(ThreadingHTTPServer):
    # A page requests its dozen stylesheets and scripts at once; the default
    # backlog of 5 refuses some of them.
    request_queue_size = 64
    daemon_threads = True


def listen(host: str, port: int | None) -> Server:
    """Bind the given port, or the first free one from 8765 when none is given."""
    if port is not None:
        return Server((host, port), Handler)
    for candidate in range(8765, 8785):
        try:
            return Server((host, candidate), Handler)
        except OSError:
            continue
    return Server((host, 0), Handler)


def check() -> int:
    """Validate every feedback file and rewrite it in canonical form."""
    failures = 0
    for path in sorted(FEEDBACK.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            clean = apply_op({"notes": {}}, {"op": "merge", "notes": doc.get("notes", {})})
            doc["notes"] = clean["notes"]
            doc["page"] = path.stem
            text = dump(doc)
            if text != path.read_text(encoding="utf-8"):
                path.write_text(text, encoding="utf-8")
                print(f"tidied  {path.name}")
            else:
                print(f"ok      {path.name} ({len(doc['notes'])} notes)")
        except (ValueError, AttributeError, BadRequest) as err:
            failures += 1
            print(f"INVALID {path.name}: {err}", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--port", type=int, help="port (default: the first free one from 8765; 0 picks any)"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)"
    )
    parser.add_argument("--open", action="store_true", help="open the index in the browser")
    parser.add_argument(
        "--check", action="store_true", help="validate and tidy feedback/*.json, then exit"
    )
    args = parser.parse_args()
    if args.check:
        return check()
    FEEDBACK.mkdir(exist_ok=True)
    server = listen(args.host, args.port)
    host, port = str(server.server_address[0]), int(server.server_address[1])
    url = f"http://{'127.0.0.1' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(f"WGS Extract mockups on {url}  (notes are saved in {FEEDBACK})", flush=True)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("Warning: listening beyond this computer; anyone who can reach it can edit notes.")
    print("Stop with Ctrl-C.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
