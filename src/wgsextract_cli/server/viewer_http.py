"""Viewer-only HTTP range and preflight responses."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .operations import RequestError
from .viewer_files import CHUNK_BYTES, byte_range, open_viewer_file

if TYPE_CHECKING:
    from .http_api import Handler


def preflight(handler: Handler) -> None:
    from .http_api import check_headers

    handler.close_connection = True
    try:
        if (
            urlsplit(handler.path).path != "/v1/viewer-file"
            or not handler.server.viewer_files
        ):
            raise RequestError(404, "not_found", "Viewer files are disabled.")
        check_headers(
            handler.headers.get,
            handler.server.hosts,
            None,
            "server.json",
            handler.server.allowed_origins,
        )
        origin = handler.headers.get("Origin")
        if not origin or handler.headers.get("Access-Control-Request-Method") not in (
            "GET",
            "HEAD",
        ):
            raise RequestError(403, "bad_origin", "Viewer preflight is refused.")
        requested = (
            handler.headers.get("Access-Control-Request-Headers", "").lower().split(",")
        )
        if any(
            value.strip() not in ("authorization", "range")
            for value in requested
            if value.strip()
        ):
            raise RequestError(
                403, "bad_headers", "Viewer preflight headers are refused."
            )
        handler._send_json(
            200,
            {},
            Access_Control_Allow_Origin=origin,
            Access_Control_Allow_Methods="GET, HEAD",
            Access_Control_Allow_Headers="Authorization, Range",
            Vary="Origin",
            Connection="close",
        )
    except RequestError as error:
        handler._send_error(error)


def serve_file(handler: Handler, query: dict[str, list[str]]) -> None:
    if not handler.server.viewer_files:
        raise RequestError(404, "not_found", "Viewer files are disabled.")
    values = query.get("path", [])
    if len(values) != 1:
        raise RequestError(400, "usage", "Supply exactly one absolute file path.")
    with open_viewer_file(values[0], handler.server.guard) as file:
        size = os.fstat(file.fileno()).st_size
        selected = byte_range(handler.headers.get("Range"), size)
        handler.send_response(
            416 if selected is None else (206 if selected[2] else 200)
        )
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Accept-Ranges", "bytes")
        origin = handler.headers.get("Origin")
        if origin:
            handler.send_header("Access-Control-Allow-Origin", origin)
            handler.send_header(
                "Access-Control-Expose-Headers",
                "Content-Length, Content-Range, Accept-Ranges",
            )
            handler.send_header("Vary", "Origin")
        if selected is None:
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        start, end, partial = selected
        remaining = max(0, end - start + 1)
        handler.send_header("Content-Length", str(remaining))
        if partial:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        handler.end_headers()
        if handler.command == "HEAD":
            return
        file.seek(start)
        while remaining:
            chunk = file.read(min(CHUNK_BYTES, remaining))
            if not chunk:
                handler.close_connection = True
                return
            handler.wfile.write(chunk)
            remaining -= len(chunk)


def openapi_path() -> dict[str, object]:
    return {
        method: {
            "operationId": f"{method}_viewer_file",
            "summary": "Read guarded viewer file bytes (default off)",
            "description": "Requires server.viewer_files and bearer authentication. "
            "Closed byte ranges stream without a size limit; whole files, suffix "
            "and open-ended ranges have a 32 MiB fallback limit.",
            "parameters": [
                {
                    "name": "path",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                },
                {"name": "Range", "in": "header", "schema": {"type": "string"}},
            ],
            "responses": {
                code: {
                    "description": description,
                    **(
                        {
                            "content": {
                                "application/octet-stream": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            }
                        }
                        if code in ("200", "206") and method == "get"
                        else {}
                    ),
                }
                for code, description in (
                    ("200", "Whole file"),
                    ("206", "Byte range"),
                    ("400", "Invalid request"),
                    ("401", "Token required"),
                    ("403", "Rejected path, Host or Origin"),
                    ("404", "Disabled or missing file"),
                    ("409", "File changed"),
                    ("416", "Unsatisfiable range"),
                )
            },
        }
        for method in ("get", "head")
    }
