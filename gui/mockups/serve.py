#!/usr/bin/env python3
"""Serve WGS Extract mockups with shared SQLite or per-page repository notes.

    python3 gui/mockups/serve.py            # http://127.0.0.1:8765/
    python3 gui/mockups/serve.py --open     # and open it in the browser
    python3 gui/mockups/serve.py --check    # validate and tidy feedback/*.json

Use --db for SQLite, --repo for GitHub snapshots, and --local-repo for
SSH-pushed ideas. POST /api/refresh requests an immediate refresh.
See README.md for the notes API, import/export and unattended hosting.
Standard library only; private-network and same-origin access only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from review_access import BadRequest
from review_db import ReviewDB
from review_http import ReviewFiles
from review_model import apply_op
from review_repo import ReviewRepo, page_paths, previews

HERE = Path(__file__).resolve().parent
FEEDBACK = HERE / "feedback"
ABOUT_JS = HERE / "shared" / "annotate" / "about.js"
PAGE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_BODY = 512 * 1024
WRITE_LOCK = threading.Lock()
DB: ReviewDB | None = None
REPO: ReviewRepo | None = None


def read_about() -> str:
    """The "about" text every feedback file starts with; shared with the
    browser's Export, so it lives in about.js as one JSON string literal."""
    match = re.search(
        r"^window\.WGSM_ABOUT = (\".*\");\s*$",
        ABOUT_JS.read_text("utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise SystemExit(f"{ABOUT_JS}: no window.WGSM_ABOUT string")
    about: str = json.loads(match.group(1))
    return about


ABOUT = read_about()


def feedback_path(page: str) -> Path:
    """feedback/<page>.json; the id pattern already rules out separators, and
    the containment check makes sure nothing resolves outside feedback/."""
    if not isinstance(page, str) or not PAGE_ID.fullmatch(page):
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"bad page id: {page!r}")
    base = os.path.realpath(FEEDBACK)
    full = os.path.realpath(os.path.join(base, page + ".json"))
    if not full.startswith(base + os.sep):
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"bad page id: {page!r}")
    return Path(full)


def idea_id(value: str) -> str:
    if not isinstance(value, str) or (
        value != "main"
        and (
            len(value) > 256
            or not re.fullmatch(r"(mockups|local)/[^\x00-\x20\x7f]+", value)
        )
    ):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "bad idea")
    if DB is None and value != "main":
        raise BadRequest(HTTPStatus.BAD_REQUEST, "preview notes require --db")
    return value


def load(page: str, idea: str = "main") -> dict[str, Any]:
    path = feedback_path(page)
    if DB:
        return DB.load(idea_id(idea), page)
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
        raise BadRequest(
            HTTPStatus.CONFLICT, f"feedback/{page}.json has no notes object"
        )
    doc.setdefault("notes", {})
    doc["page"] = page
    return doc


def dump(doc: dict[str, Any]) -> str:
    """Canonical file text: the about text first (sorted keys put it there)."""
    idea = doc.get("idea", "main")
    about = (
        ABOUT
        if idea == "main"
        else ABOUT.replace("on branch main.", f"on branch {idea}.")
    )
    doc = {**doc, "about": about}
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def save(page: str, doc: dict[str, Any], idea: str = "main") -> None:
    if DB:
        DB.save(idea_id(idea), page, doc)
        return
    path = feedback_path(page)
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(dump(doc))
    os.replace(tmp, path)


def documents() -> list[dict[str, Any]]:
    if DB:
        docs = DB.documents()
    else:
        docs = [
            {**load(path.stem), "idea": "main"}
            for path in sorted(FEEDBACK.glob("*.json"))
            if not path.stem.startswith("_")
        ]
    for doc in docs:
        idea = doc.get("idea", "main")
        root = REPO.roots.get(idea) if REPO else HERE
        path = page_paths(root).get(doc["page"]) if root else None
        name = (
            next((name for name, branch in REPO.branches.items() if branch == idea), "")
            if REPO
            else ""
        )
        base = (
            "/"
            if idea == "main"
            else (f"/{name}/" if idea.startswith("local/") else f"/preview/{name}/")
        )
        doc["url"] = base + (path or "index.html")
    return docs


def export_all() -> dict[str, Any]:
    return {
        "about": ABOUT + ' An all-notes export wraps page documents in "pages"; '
        'each has an "idea" (main, a mockups/* GitHub branch or a local/* SSH-pushed branch) and a server-relative "url". '
        "Apply each document to its own idea; import merges by note id and updated timestamp.",
        "schema": 1,
        "pages": documents(),
    }


def import_all(body: Any) -> None:
    if not isinstance(body, dict) or not isinstance(body.get("pages"), list):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "expected pages list")
    pending: dict[tuple[str, str], dict[str, Any]] = {}
    for incoming in body["pages"]:
        if not isinstance(incoming, dict):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "expected page object")
        idea = idea_id(incoming.get("idea", "main"))
        page = incoming.get("page", "")
        feedback_path(page)
        key = (idea, page)
        doc = pending.get(key) or load(page, idea)
        doc = apply_op(doc, {"op": "merge", "notes": incoming.get("notes")})
        doc["title"] = str(incoming.get("title", doc.get("title", "")))[:200]
        pending[key] = doc
    if DB:
        with DB.connect() as db:
            for (idea, page), doc in pending.items():
                DB.write(db, idea, page, doc)
    else:
        for (_, page), doc in pending.items():
            save(page, doc)


def summary(idea: str = "main") -> dict[str, Any]:
    pages = []
    for doc in documents():
        if doc.get("idea", "main") != idea:
            continue
        notes = list(doc["notes"].values())
        resolved = sum(1 for n in notes if n.get("status") == "resolved")
        pages.append(
            {
                "open": len(notes) - resolved,
                "page": doc["page"],
                "resolved": resolved,
                "title": doc.get("title", ""),
                "updated": max((n.get("updated", "") for n in notes), default=""),
            }
        )
    return {"pages": pages}


class Handler(ReviewFiles):
    server_version = "WGSEMockups/1"

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

    def read_json(self) -> Any:
        if (self.headers.get("Content-Type") or "").split(";")[0] != "application/json":
            raise BadRequest(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY:
            raise BadRequest(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body missing or too large"
            )
        try:
            return json.loads(self.rfile.read(length))
        except ValueError as err:  # also covers JSONDecodeError and bad UTF-8
            raise BadRequest(HTTPStatus.BAD_REQUEST, f"bad JSON: {err}") from err

    def api(self, method: str) -> None:
        try:
            self.check_origin()
            route = urlsplit(self.path).path.rstrip("/").split("/")[2:]
            query = parse_qs(urlsplit(self.path).query)
            idea = idea_id(query.get("idea", ["main"])[0])
            if method == "GET" and route == ["health"]:
                self.reply({"ok": True, "writable": True, "shared": DB is not None})
            elif method == "POST" and route == ["refresh"] and REPO:
                self.read_json()
                REPO.request_refresh()
                self.reply({"ok": True, "queued": True}, HTTPStatus.ACCEPTED)
            elif method == "GET" and route == ["export"]:
                with WRITE_LOCK:
                    self.reply(export_all())
            elif method == "POST" and route == ["import"]:
                body = self.read_json()
                with WRITE_LOCK:
                    import_all(body)
                self.reply({"ok": True})
            elif method == "GET" and route == ["notes"]:
                with WRITE_LOCK:
                    docs = documents()
                notes = [
                    {
                        **note,
                        "idea": doc.get("idea", "main"),
                        "page": doc["page"],
                        "title": doc.get("title", ""),
                        "url": doc["url"],
                    }
                    for doc in docs
                    for note in doc["notes"].values()
                ]
                for field in ("idea", "page", "status", "author"):
                    if field in query:
                        notes = [n for n in notes if n.get(field) == query[field][0]]
                self.reply({"notes": notes})
            elif method == "GET" and route == ["feedback"]:
                self.reply(summary(idea))
            elif len(route) == 2 and route[0] == "feedback" and method == "GET":
                self.reply(load(route[1], idea))
            elif len(route) == 2 and route[0] == "feedback" and method == "POST":
                change = self.read_json()
                with WRITE_LOCK:
                    doc = apply_op(load(route[1], idea), change)
                    if isinstance(change, dict) and isinstance(
                        change.get("title"), str
                    ):
                        doc["title"] = change["title"][:200]
                    save(route[1], doc, idea)
                self.reply(doc)
            else:
                raise BadRequest(HTTPStatus.NOT_FOUND, "no such endpoint")
        except BadRequest as err:
            self.reply({"error": {"message": str(err)}}, err.status)
        # A bug, not a bad request: answer rather than drop the connection.
        except Exception as err:  # noqa: BLE001
            self.log_error("notes API error: %r", err)
            self.reply(
                {"error": {"message": f"server error: {err}"}},
                HTTPStatus.INTERNAL_SERVER_ERROR,
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
            clean = apply_op(
                {"notes": {}}, {"op": "merge", "notes": doc.get("notes", {})}
            )
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
    global DB, REPO, FEEDBACK
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--port",
        type=int,
        help="port (default: the first free one from 8765; 0 picks any)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)"
    )
    parser.add_argument(
        "--open", action="store_true", help="open the index in the browser"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate and tidy feedback/*.json, then exit",
    )
    parser.add_argument("--db", type=Path, help="shared SQLite notes database")
    parser.add_argument(
        "--repo", type=Path, help="read-only bare clone for main and previews"
    )
    parser.add_argument(
        "--local-repo",
        type=Path,
        help="separate bare repository for SSH-pushed previews",
    )
    parser.add_argument(
        "--cache", type=Path, help="static snapshots (default: beside --db)"
    )
    parser.add_argument("--refresh-minutes", type=float, default=5)
    parser.add_argument(
        "--allow-host", action="append", default=[], help="allowed host:port"
    )
    parser.add_argument("--feedback-dir", type=Path, default=FEEDBACK)
    parser.add_argument(
        "--import-feedback",
        type=Path,
        help="import per-page files into main, then exit",
    )
    parser.add_argument(
        "--export-feedback",
        type=Path,
        help="write per-idea repo-format files, then exit",
    )
    args = parser.parse_args()
    if args.local_repo and not args.repo:
        parser.error("--local-repo requires --repo")
    FEEDBACK = args.feedback_dir
    if args.db:
        DB = ReviewDB(args.db)
    if args.import_feedback:
        with WRITE_LOCK:
            import_all(
                {
                    "pages": [
                        {
                            **json.loads(p.read_text("utf-8")),
                            "page": p.stem,
                            "idea": "main",
                        }
                        for p in sorted(args.import_feedback.glob("*.json"))
                        if not p.stem.startswith("_")
                    ]
                }
            )
        return 0
    if args.export_feedback:
        for doc in documents():
            dest = args.export_feedback
            if doc.get("idea", "main") != "main":
                branch = idea_id(doc["idea"])
                name = (
                    previews.slug(branch)
                    + "-"
                    + hashlib.sha256(branch.encode()).hexdigest()[:10]
                )
                dest /= "preview/" + name
            dest.mkdir(parents=True, exist_ok=True)
            (dest / (doc["page"] + ".json")).write_text(
                dump({k: v for k, v in doc.items() if k != "url"}), encoding="utf-8"
            )
        return 0
    if args.check:
        return check()
    FEEDBACK.mkdir(exist_ok=True)
    if args.repo:
        if not args.db or args.refresh_minutes <= 0:
            parser.error("--repo requires --db and positive --refresh-minutes")
        REPO = ReviewRepo(
            args.repo,
            args.cache or args.db.parent / "cache",
            args.refresh_minutes * 60,
            local_repo=args.local_repo,
        )
        Handler.repository = REPO
    Handler.allow_hosts = {host.lower() for host in args.allow_host}
    server = listen(args.host, args.port)
    host, port = str(server.server_address[0]), int(server.server_address[1])
    url = f"http://{'127.0.0.1' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(
        f"WGS Extract mockups on {url}  (notes are saved in {DB.path if DB else FEEDBACK})",
        flush=True,
    )
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "Warning: listening beyond this computer; anyone who can reach it can edit notes."
        )
    print("Stop with Ctrl-C.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if REPO:
            REPO.stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
