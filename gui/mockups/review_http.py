"""Private-network static serving and same-origin API boundary."""

from __future__ import annotations

from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from os import PathLike
from pathlib import Path
from typing import Any
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

    def translate_path(self, path: str) -> str:
        parts = unquote(urlsplit(path).path).strip("/").split("/")
        root = HERE
        if self.repository:
            if parts == ["preview"] or parts == ["preview", ""]:
                return str(self.repository.cache / "preview-index.html")
            idea = "main"
            if parts[0] == "preview" and len(parts) > 1:
                idea, parts = self.repository.branches.get(parts[1], ""), parts[2:]
            root = self.repository.roots.get(idea, HERE / ".missing")
        rel = Path(*parts) if parts and parts[0] else Path("index.html")
        # Review tooling comes from the running app, not old idea branches.
        if (
            rel == Path("notes.html")
            or rel == Path("shared/chrome.js")
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
