"""Interrupt urllib HTTP reads without changing the normal socket timeout."""

from __future__ import annotations

import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from http.client import HTTPConnection, HTTPException, HTTPResponse, HTTPSConnection
from typing import Any
from urllib.request import HTTPHandler, HTTPSHandler, Request, build_opener

from wgsextract_cli.core.download_progress import CancelEvent, DownloadCancelled
from wgsextract_cli.core.execution_context import context_thread


@contextmanager
def cancellable_http(
    request: Request | str, timeout: float, cancel: CancelEvent
) -> Iterator[HTTPResponse]:
    """Keep the live transport visible before urllib reads headers or the body."""
    stopped = threading.Event()
    transport: socket.socket | None = None

    def interrupt() -> None:
        while not stopped.wait(0.1):
            sock = transport
            if cancel.is_set() and sock is not None:
                with suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
                return

    def check() -> None:
        if cancel.is_set():
            raise DownloadCancelled("Download cancelled by user.")

    def watch(connection: HTTPConnection) -> None:
        nonlocal transport
        transport = connection.sock
        if cancel.is_set():
            connection.close()
            check()

    class HttpConnection(HTTPConnection):
        def connect(self) -> None:
            check()
            super().connect()
            watch(self)

    class HttpsConnection(HTTPSConnection):
        def connect(self) -> None:
            check()
            super().connect()
            watch(self)

    class HttpHandler(HTTPHandler):
        def http_open(self, req: Request) -> Any:
            return self.do_open(HttpConnection, req)

    class HttpsHandler(HTTPSHandler):
        _context: ssl.SSLContext | None

        def https_open(self, req: Request) -> Any:
            return self.do_open(HttpsConnection, req, context=self._context)

    check()
    thread = context_thread(target=interrupt, daemon=True)
    thread.start()
    try:
        with build_opener(HttpHandler(), HttpsHandler()).open(
            request, timeout=timeout
        ) as response:
            check()
            yield response
    except (OSError, HTTPException):
        check()
        raise
    finally:
        stopped.set()
        thread.join()
