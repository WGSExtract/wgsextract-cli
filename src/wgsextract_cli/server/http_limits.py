"""Bound accepted connections and input deadlines, not authenticated responses."""

from __future__ import annotations

import math
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from threading import BoundedSemaphore, Lock, Timer
from typing import Any


class LimitedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        max_connections: int,
        header_timeout_seconds: float,
    ) -> None:
        if not 1 <= max_connections <= 256:
            raise ValueError("server.max_connections must be between 1 and 256.")
        if not math.isfinite(header_timeout_seconds) or header_timeout_seconds <= 0:
            raise ValueError("server.header_timeout_seconds must be positive.")
        self.header_timeout_seconds = header_timeout_seconds
        self._connections = BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def server_bind(self) -> None:
        # HTTPServer.server_bind calls getfqdn; numeric listeners must not use DNS.
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = self.server_address[1]

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        if not self._connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            # SIGINT can unwind dispatch after the worker already released its
            # slot. Don't release it twice and mask the shutdown with ValueError.
            self._connections.release()
            raise

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()


class LimitedHandler(BaseHTTPRequestHandler):
    server: LimitedHTTPServer
    response_started = False

    def setup(self) -> None:
        super().setup()
        self._input_lock = Lock()
        self._input_generation = 0
        self._input_active = False
        self._input_expired = False
        self._input_timer: Timer | None = None

    def handle_one_request(self) -> None:
        self.response_started = False
        with self._input_lock:
            self._input_generation += 1
            generation = self._input_generation
            self._input_active = True
            self._input_expired = False
            timer = Timer(
                self.server.header_timeout_seconds, self._expire_input, (generation,)
            )
            timer.daemon = True
            self._input_timer = timer
        timer.start()
        try:
            super().handle_one_request()
        finally:
            self._finish_input()

    def parse_request(self) -> bool:
        try:
            accepted = super().parse_request()
        finally:
            expired = self._finish_input()
        return accepted and not expired

    def _finish_input(self) -> bool:
        with self._input_lock:
            self._input_active = False
            timer, self._input_timer = self._input_timer, None
            expired = self._input_expired
        if timer is not None:
            timer.cancel()
        return expired

    def _expire_input(self, generation: int) -> None:
        with self._input_lock:
            if not self._input_active or self._input_generation != generation:
                return
            self._input_active = False
            self._input_expired = True
            self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
