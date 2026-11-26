"""A local HTTP server with byte ranges, for download tests that never reach
the network."""

import hashlib
import secrets
import select
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def _etag(data: bytes) -> str:
    return f'"{hashlib.md5(data).hexdigest()}"'


def _date(data: bytes) -> str:
    """A modification date that differs between contents."""
    return formatdate(
        1_600_000_000 + int(hashlib.md5(data).hexdigest()[:6], 16), usegmt=True
    )


class DownloadHTTPServer(ThreadingHTTPServer):
    """Leave listen-queue room for parallel segmented-download connections."""

    request_queue_size = 64


class RangeServer:
    """Serves ``files[path]``, or ``data`` for any other path (404 when None),
    with byte ranges, and records each request.

    drops: GETs left to cut off half-way; drop_paths: paths whose next GET
    is cut off half-way, once. drop_starts: ranged GETs starting at
    these offsets are always cut off. head_status/get_status: answer HEAD/GET
    with this error; statuses[path]: answer requests for path with it.
    get_errors[path]: sequence of error statuses to return before serving GETs.
    etags: send each file's ETag (a hash of its content), as S3 does.
    dates: send a Last-Modified date that changes with the content.
    weak_etags: send ETags marked weak (W/"...").
    fixed_date: send it as every reply's Date and, with dates, as the
    Last-Modified of any content: a file rewritten within the second.
    An If-Range naming another version than the served one gets the whole
    file (200), as from real servers, unless ignore_if_range.
    delays[path]: seconds to wait before answering a GET for path.
    body_pauses[path]: (prefix bytes, started, release) to stall a GET;
    None as the prefix size stalls before headers instead.
    body_disconnects[path]: signal if a client disconnects during the pause.
    headers[path]: extra headers sent with path (as x-goog-hash on GCS).
    transform_get: alter a response body without changing its reported revision.
    """

    def __init__(self, data: bytes | None = b"", files: dict[str, bytes] | None = None):
        self.data = data
        self.files = dict(files or {})
        self.drops = 0
        self.drop_paths: set[str] = set()
        self.drop_starts: set[int] = set()
        self.ignore_ranges = False
        self.get_status: int | None = None
        self.head_status: int | None = None
        self.bare_416 = False  # leave Content-Range out of 416s, as Ensembl does
        self.statuses: dict[str, int] = {}
        self.get_errors: dict[str, list[int]] = {}
        self.etags = False
        self.dates = False
        self.ignore_if_range = False
        self.weak_etags = False
        self.fixed_date: str | None = None
        self.delays: dict[str, float] = {}
        self.body_pauses: dict[
            str, tuple[int | None, threading.Event, threading.Event]
        ] = {}
        self.body_disconnects: dict[str, threading.Event] = {}
        self.headers: dict[str, dict[str, str]] = {}
        self.transform_get: Callable[[int, bytes], bytes] | None = None
        self.requests: list[tuple[str, str | None]] = []
        # (method, request target with any query, headers) of each request.
        self.raw_requests: list[tuple[str, str, dict[str, str]]] = []
        self.get_paths: list[str] = []
        # Other software on the host may probe any new listening port (one
        # sent `GET /` with `Range: bytes=0-16383`, #987); only requests under
        # this unguessable prefix come from the test, so only they are served
        # and recorded.
        self.prefix = f"/{secrets.token_hex(8)}/"
        self.paths: list[str] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _own_path(self) -> str | None:
                """The requested path below the server's prefix; None, after a
                404, for a request from some other client on the host."""
                path = urlsplit(self.path).path
                if not path.startswith(server.prefix):
                    self.send_error(404)
                    return None
                server.raw_requests.append(
                    (self.command, self.path, dict(self.headers.items()))
                )
                return path.removeprefix(server.prefix)

            def _body(self, path: str) -> bytes | None:
                server.paths.append(path)
                if path in server.statuses:
                    self.send_error(server.statuses[path])
                    return None
                return server.files.get(path, server.data)

            def date_time_string(self, timestamp=None):
                return server.fixed_date or super().date_time_string(timestamp)

            def _etag(self, data: bytes, path: str) -> None:
                if server.etags:
                    weak = "W/" if server.weak_etags else ""
                    self.send_header("ETag", weak + _etag(data))
                if server.dates:
                    self.send_header("Last-Modified", server._date(data))
                for name, value in server.headers.get(path, {}).items():
                    self.send_header(name, value)

            def _pause(
                self, path: str, started: threading.Event, release: threading.Event
            ) -> None:
                started.set()
                if disconnected := server.body_disconnects.get(path):
                    deadline = time.monotonic() + 10
                    while not release.is_set() and time.monotonic() < deadline:
                        readable, _, _ = select.select([self.connection], [], [], 0.05)
                        if readable:
                            try:
                                if self.connection.recv(1):
                                    continue
                            except ConnectionError:
                                pass
                            disconnected.set()
                            break
                else:
                    release.wait(10)

            def do_HEAD(self):
                path = self._own_path()
                if path is None:
                    return
                server.requests.append(("HEAD", None))
                data = self._body(path)
                if data is None and path in server.statuses:
                    return
                if server.head_status or data is None:
                    self.send_error(server.head_status or 404)
                    return
                self.send_response(200)
                self._etag(data, path)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()

            def do_GET(self):
                path = self._own_path()
                if path is None:
                    return
                time.sleep(server.delays.get(path, 0))
                spec = self.headers.get("Range")
                server.requests.append(("GET", spec))
                server.get_paths.append(path)
                errors = server.get_errors.get(path)
                if errors:
                    self.send_error(errors.pop(0))
                    return
                data = self._body(path)
                if data is None and path in server.statuses:
                    return
                if server.get_status or data is None:
                    self.send_error(server.get_status or 404)
                    return
                start = 0
                body, code = data, 200
                if_range = self.headers.get("If-Range")
                if (
                    if_range is not None
                    and not server.ignore_if_range
                    and not (
                        (server.etags and if_range == _etag(data))
                        or (server.dates and if_range == server._date(data))
                    )
                ):
                    spec = None
                if spec and not server.ignore_ranges:
                    first, _, last = spec.removeprefix("bytes=").partition("-")
                    start = int(first)
                    if start >= len(data):
                        self.send_response(416)
                        if not server.bare_416:
                            self.send_header("Content-Range", f"bytes */{len(data)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    end = int(last) + 1 if last else len(data)
                    body, code = data[start:end], 206
                if server.transform_get is not None:
                    body = server.transform_get(start, body)
                cut_path = path in server.drop_paths
                server.drop_paths.discard(path)
                cut = (
                    server.drops > 0
                    or cut_path
                    or (code == 206 and start in server.drop_starts)
                )
                pause = server.body_pauses.get(path)
                if pause and pause[0] is None:
                    self._pause(path, pause[1], pause[2])
                    return
                self.send_response(code)
                self._etag(data, path)
                self.send_header("Accept-Ranges", "bytes")
                if code == 206:
                    last = start + len(body) - 1
                    self.send_header(
                        "Content-Range", f"bytes {start}-{last}/{len(data)}"
                    )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if cut:
                    if not cut_path:
                        server.drops = max(0, server.drops - 1)
                    self.wfile.write(body[: len(body) // 2])
                    self.close_connection = True
                    return
                if pause:
                    length, started, release = pause
                    self.wfile.write(body[:length])
                    self.wfile.flush()
                    self._pause(path, started, release)
                    return
                self.wfile.write(body)

        self.httpd = DownloadHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}{self.prefix[:-1]}"
        self.url = self.url_for("hs38.fa.gz")
        # A short poll keeps shutdown() from waiting out the default half second.
        threading.Thread(
            target=self.httpd.serve_forever, args=(0.01,), daemon=True
        ).start()

    def _date(self, data: bytes) -> str:
        return self.fixed_date or _date(data)

    def url_for(self, path: str) -> str:
        return f"{self.base}/{path}"

    def leave_partial(self, url: str, dest: Path, kept: bytes) -> None:
        """Leave dest.partial holding kept, as a download of url stopped part
        way through leaves it: with the record of the served version that the
        next run resumes by. Forgets the requests that made it."""
        from wgsextract_cli.core.ref_library import downloads

        cancel = threading.Event()
        assert not downloads.download_file(
            url,
            str(dest),
            progress_callback=lambda *_: cancel.set(),
            cancel_event=cancel,
            use_cache=False,
            use_mirrors=False,
        )
        Path(f"{dest}.partial").write_bytes(kept)
        self.requests.clear()
        self.get_paths.clear()
        self.paths.clear()

    def gets(self) -> list[str | None]:
        return [spec for method, spec in self.requests if method == "GET"]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@contextmanager
def serving(
    data: bytes | None = b"", files: dict[str, bytes] | None = None
) -> Iterator[RangeServer]:
    server = RangeServer(data, files)
    try:
        yield server
    finally:
        server.close()
