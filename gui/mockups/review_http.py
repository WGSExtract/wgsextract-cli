"""Private-network static serving and same-origin API boundary."""

from __future__ import annotations

import html
import io
import json
import re
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from os import PathLike
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlsplit

from review_access import BadRequest, allowed_client
from review_repo import ReviewRepo, previews

HERE = Path(__file__).resolve().parent


class ReviewFiles(SimpleHTTPRequestHandler):
    repository: ReviewRepo | None = None
    allow_hosts: set[str] = set()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(HERE), **kwargs)

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        if not allowed_client(self.client_address[0]):
            self.send_error(HTTPStatus.FORBIDDEN, "private clients only")
            return False
        return True

    def request_idea(self) -> str:
        parts = unquote(urlsplit(self.path).path).strip("/").split("/")
        if self.repository and len(parts) > 1:
            if parts[0] == "preview":
                return self.repository.branches.get(parts[1], "")
            if parts[0] == "local":
                return self.repository.branches.get("local/" + parts[1], "")
        return "main"

    def send_head(self) -> BinaryIO | None:
        if self.repository:
            with self.repository.publish_lock:
                target = Path(self.translate_path(self.path))
                idea = self.request_idea()
        else:
            target = Path(self.translate_path(self.path))
            idea = self.request_idea()
        if urlsplit(self.path).path.endswith("/shared/version.js"):
            version = {}
            if target.is_file():
                try:
                    version = json.loads(
                        target.read_text("utf-8")
                        .removeprefix("window.WGSM_VERSION = ")
                        .strip()
                        .removesuffix(";")
                    )
                except ValueError:
                    pass
            version.update(hosted=True, idea=idea)
            data = ("window.WGSM_VERSION = " + json.dumps(version) + ";\n").encode()
            content_type = "application/javascript; charset=utf-8"
        elif target.suffix == ".html" and target.is_file():
            text = target.read_text("utf-8")
            marker = (
                '<meta name="wgsm-review-server" content="hosted" data-idea="'
                + html.escape(idea, quote=True)
                + '">\n'
            )
            head = re.search(r"</head\s*>", text, re.IGNORECASE)
            body = re.search(r"<body\b", text, re.IGNORECASE)
            doctype = re.match(r"\s*<!doctype[^>]*>", text, re.IGNORECASE)
            insert = 0
            if head:
                insert = head.start()
            elif body:
                insert = body.start()
            elif doctype:
                insert = doctype.end()
            text = text[:insert] + marker + text[insert:]
            data = text.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            return super().send_head()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        return io.BytesIO(data)

    def translate_path(self, path: str) -> str:
        parts = unquote(urlsplit(path).path).strip("/").split("/")
        root = HERE
        if self.repository:
            if parts in (["preview"], ["preview", ""], ["local"], ["local", ""]):
                return str(self.repository.cache / "preview-index.html")
            idea = "main"
            if parts[0] == "preview" and len(parts) > 1:
                idea, parts = self.repository.branches.get(parts[1], ""), parts[2:]
            elif parts[0] == "local" and len(parts) > 1:
                idea, parts = (
                    self.repository.branches.get("local/" + parts[1], ""),
                    parts[2:],
                )
            root = self.repository.roots.get(idea, HERE / ".missing")
        rel = Path(*parts) if parts and parts[0] else Path("index.html")
        if (
            not root.is_dir()
            and rel.parts
            and rel.parts[0] in ("shared", "design-system", "icons")
        ):
            # A cold offline start still needs the All notes runtime and styles.
            root = HERE
        # Review tooling comes from the running app, not old idea branches.
        if (
            rel == Path("notes.html")
            or rel in (Path("shared/chrome.js"), Path("shared/core.js"))
            or rel.parts[:2] == ("shared", "annotate")
        ):
            root = HERE
        target = root / rel
        if target.is_dir():
            target /= "index.html"
        if (
            ".." in rel.parts
            or any(p.startswith(".") for p in rel.parts)
            or target.suffix not in previews.STATIC
            or not target.resolve().is_relative_to(root.resolve())
        ):
            return str(HERE / ".missing")
        return str(target)

    def list_directory(self, path: str | PathLike[str]) -> None:
        self.send_error(HTTPStatus.FORBIDDEN, "directory listing disabled")
        return None

    def check_origin(self) -> None:
        host = (self.headers.get("Host") or "").lower()
        address = self.server.server_address
        assert isinstance(address, tuple)
        port = address[1]
        allowed = self.allow_hosts | {
            f"{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]")
        }
        if host not in allowed:
            raise BadRequest(HTTPStatus.FORBIDDEN, "bad Host header")
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != "http" or parsed.netloc.lower() != host:
                raise BadRequest(HTTPStatus.FORBIDDEN, "cross-site request refused")
