"""The HTTP/JSON front end: ``/v1`` on 127.0.0.1, with a bearer token.

Every request but ``GET /v1/health`` needs ``Authorization: Bearer <token>``;
requests naming another host (DNS rebinding) or sent from a web page of
another origin are refused, and so are POST bodies that aren't JSON.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import math
import re
import socket
import sys
from collections.abc import Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlsplit

from wgsextract_cli import __version__

from .http_limits import LimitedHandler, LimitedHTTPServer
from .job_logs import read_job_log
from .jobs import Job, JobManager
from .listener import exact_headers, parse_listener
from .operations import PathGuard, RequestError, find, operations, params_from_json
from .queries import QUERIES
from .transport import Health, Index, Jobs, OperationDescription, Operations
from .transport import Job as JobView

MAX_BODY_BYTES = 1 << 20
BODY_TIMEOUT_SECONDS = 10.0
MAX_WAIT_SECONDS = 600.0
SSE_KEEPALIVE_SECONDS = 15.0


class Server(LimitedHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        port: int,
        token: str,
        manager: JobManager,
        guard: PathGuard,
        *,
        listen: str = "127.0.0.1",
        allowed_hosts: frozenset[str] = frozenset(),
        allowed_origins: frozenset[str] = frozenset(),
        viewer_files: bool = False,
        public_url: str | None = None,
        max_connections: int = 32,
        header_timeout_seconds: float = 10.0,
    ) -> None:
        listener = parse_listener(listen, port)
        if (not listener.loopback or viewer_files) and not token.strip():
            raise ValueError(
                "Non-loopback listening and viewer files require bearer token authentication."
            )
        if not listener.loopback and not allowed_hosts:
            raise ValueError("Non-loopback listening requires server.allowed_hosts.")
        self.loopback = listener.loopback
        self.allowed_hosts = exact_headers(tuple(allowed_hosts), origins=False)
        self.allowed_origins = exact_headers(tuple(allowed_origins), origins=True)
        self.viewer_files = viewer_files
        self.public_url: str | None = None
        if public_url is not None:
            if self.loopback:
                raise ValueError(
                    "server.public_url is only for non-loopback listeners."
                )
            origin = exact_headers((public_url,), origins=True)
            advertised = urlsplit(next(iter(origin)))
            try:
                address = ipaddress.ip_address(advertised.hostname or "")
            except ValueError:
                address = None
            if address is not None and address.is_unspecified:
                raise ValueError(
                    "server.public_url cannot advertise a wildcard address."
                )
            host = (
                str(address)
                if address is not None
                else (advertised.hostname or "").encode("idna").decode("ascii")
            )
            authority = f"[{host}]" if ":" in host else host
            if advertised.port is not None and advertised.port != (
                443 if advertised.scheme == "https" else 80
            ):
                authority += f":{advertised.port}"
            if authority not in self.allowed_hosts:
                raise ValueError(
                    "server.public_url authority must be in server.allowed_hosts."
                )
            self.public_url = f"{advertised.scheme}://{authority}"
        elif not self.loopback:
            raise ValueError(
                "Non-loopback listening requires a connectable server.public_url."
            )
        if ":" in listener.address:
            self.address_family = socket.AF_INET6
        super().__init__(
            (listener.address, listener.port),
            Handler,
            max_connections=max_connections,
            header_timeout_seconds=header_timeout_seconds,
        )
        self.token = token
        self.manager = manager
        self.guard = guard

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A client that drops its connection (Windows resets idle keep-alive
        # connections) isn't a server error worth a traceback.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)

    @property
    def url(self) -> str:
        return self.public_url or self.bind_url

    @property
    def bind_url(self) -> str:
        host = str(self.server_address[0])
        host = f"[{host}]" if ":" in host else host
        return f"http://{host}:{self.server_address[1]}"

    @property
    def hosts(self) -> frozenset[str]:
        if not self.loopback:
            return self.allowed_hosts
        return local_hosts(self.server_address[1]) | frozenset(
            {self.bind_url.removeprefix("http://")}
        )


def job_view(job: Job, query: dict[str, list[str]]) -> JobView:
    return job.view(full=_flag(query, "full"))


def _flag(query: dict[str, list[str]], name: str) -> bool:
    return query.get(name, ["0"])[-1].lower() in ("1", "true", "yes")


def _wait(query: dict[str, list[str]]) -> float:
    try:
        seconds = float(query.get("wait", ["0"])[-1])
        if not math.isfinite(seconds):
            raise ValueError
    except ValueError:
        raise RequestError(400, "usage", "wait must be a number of seconds.") from None
    return min(max(seconds, 0.0), MAX_WAIT_SECONDS)


def check_headers(
    header: Callable[[str], str | None],
    hosts: frozenset[str],
    token: str | None,
    discovery: str,
    origins: frozenset[str] | None = None,
) -> None:
    """Refuse a request naming another host (DNS rebinding), sent from a web
    page of another origin, or, unless ``token`` is None, without the bearer
    token ``wgsextract serve`` printed and saved in ``discovery``."""
    if (header("Host") or "") not in hosts:
        raise RequestError(403, "bad_host", "Unexpected Host header.")
    origin = header("Origin")
    accepted_origins = (
        origins
        if origins is not None
        else frozenset(f"http://{host}" for host in hosts)
    )
    if origin and origin not in accepted_origins:
        raise RequestError(403, "bad_origin", "Cross-origin requests are refused.")
    if token is None:
        return
    scheme, _, sent = (header("Authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        sent.strip().encode(), token.encode()
    ):
        raise RequestError(
            401,
            "unauthorized",
            "Send 'Authorization: Bearer <token>'; `wgsextract serve` "
            f"printed the token and saved it in its {discovery}.",
        )


def local_hosts(port: int) -> frozenset[str]:
    """The Host headers a server on 127.0.0.1:``port`` answers."""
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})


Route = Callable[["Handler", re.Match[str], dict[str, list[str]]], None]


class Handler(LimitedHandler):
    server: Server
    protocol_version = "HTTP/1.1"
    server_version = f"wgsextract/{__version__}"
    _response_status = 200

    # Plumbing ---------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        logging.debug("HTTP %s", format % args)

    def end_headers(self) -> None:
        if self._response_status >= 200:
            self.response_started = True
        super().end_headers()

    def send_response_only(self, code: int, message: str | None = None) -> None:
        self._response_status = code
        super().send_response_only(code, message)

    def abort_response(self) -> None:
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _send_json(self, status: int, body: object, **headers: str) -> None:
        data = json.dumps(body, separators=(",", ":"), default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_error(self, error: RequestError) -> None:
        if self.response_started:
            self.abort_response()
            return
        # The request's body may be unread: don't parse it as the next request.
        self.close_connection = True
        headers = {"Connection": "close"}
        origin = self.headers.get("Origin")
        if (
            self.path.split("?", 1)[0] == "/v1/viewer-file"
            and self.server.viewer_files
            and self.headers.get("Host") in self.server.hosts
            and origin in self.server.allowed_origins
            and len(self.headers.get_all("Origin", [])) == 1
            and origin is not None
        ):
            headers["Access_Control_Allow_Origin"] = origin
            headers["Vary"] = "Origin"
        if error.status == 401:
            headers["WWW_Authenticate"] = "Bearer"
        self._send_json(error.status, error.to_dict(), **headers)

    def _check_request(self, path: str) -> None:
        for name in ("Host", "Origin", "Authorization", "Range", "X-WGSE-Job-ID"):
            if len(self.headers.get_all(name, [])) > 1:
                raise RequestError(
                    400, "bad_headers", "Duplicate request headers are refused."
                )
        viewer = path == "/v1/viewer-file"
        check_headers(
            self.headers.get,
            self.server.hosts,
            None
            if path == "/v1/health" and self.server.loopback
            else self.server.token,
            "server.json",
            self.server.allowed_origins if viewer or not self.server.loopback else None,
        )

    def _read_body(self) -> bytes:
        if "Transfer-Encoding" in self.headers:
            raise RequestError(400, "bad_length", "Transfer-Encoding is not supported.")
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            raise RequestError(411, "bad_length", "Content-Length is required.")
        value = lengths[0].strip(" \t")
        try:
            length = (
                int(value)
                if len(lengths) == 1 and re.fullmatch(r"[0-9]+", value)
                else -1
            )
        except ValueError:
            length = -1
        if length < 0:
            raise RequestError(400, "bad_length", "Invalid Content-Length.")
        if length > MAX_BODY_BYTES:
            raise RequestError(413, "too_large", "The request body is too large.")
        raw = self.read_body(length, timeout=BODY_TIMEOUT_SECONDS) if length else b""
        if len(raw) != length:
            raise RequestError(400, "bad_length", "Incomplete request body.")
        return raw

    def _decode_body(self, raw: bytes) -> object:
        if not raw:
            return {}
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";")[0].strip().lower() != "application/json":
            raise RequestError(
                415, "not_json", "Send the parameters as application/json."
            )
        try:
            return json.loads(raw)
        except ValueError as e:
            raise RequestError(400, "bad_json", f"Invalid JSON: {e}") from None

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        try:
            try:
                self._check_request(url.path)
                for route_method, pattern, route in ROUTES:
                    match = pattern.fullmatch(url.path)
                    if match and route_method == method:
                        route(self, match, query)
                        return
                raise RequestError(404, "not_found", f"No route {method} {url.path}.")
            except RequestError as e:
                self._send_error(e)
            except ConnectionError:
                raise
            except Exception as e:
                logging.exception(f"{method} {url.path} failed")
                self._send_error(RequestError(500, "internal", f"Internal error: {e}"))
        except ConnectionError:
            # The client went away (closed, reset or aborted); nobody to answer.
            self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_OPTIONS(self) -> None:  # noqa: N802
        from .viewer_http import preflight

        preflight(self)

    def viewer_file(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        from .viewer_http import serve_file

        serve_file(self, query)

    # Routes ----------------------------------------------------------------

    def health(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        self._send_json(200, Health(ok=True, version=__version__))

    def index(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        self._send_json(
            200,
            Index(
                name="wgsextract",
                version=__version__,
                ops="/v1/ops",
                jobs="/v1/jobs",
                openapi="/v1/openapi.json",
                lookups=[f"/v1/{q.route}" for q in QUERIES.values()],
                allowed_paths=[str(p) for p in self.server.guard.roots],
            ),
        )

    def openapi(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        self._send_json(
            200,
            openapi_document(
                self.server.url, health_requires_auth=not self.server.loopback
            ),
        )

    def lookup(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        (lookup,) = (q for q in QUERIES.values() if q.route == match["route"])
        arguments = lookup.from_query_string(query)
        self._send_json(200, lookup.run(self.server.guard, arguments))

    def list_ops(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        self._send_json(
            200, Operations(ops=[op.describe() for op in operations().values()])
        )

    def describe_op(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        op = find(match["op"])
        self._send_json(
            200,
            OperationDescription(
                **op.describe(), command=op.command, params=op.schema()
            ),
        )

    def run_op(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        # Closing with unread POST data can reset the connection before the
        # client receives the conflict. Read the body without validating JSON yet.
        raw = self._read_body()
        job_id = self.headers.get("X-WGSE-Job-ID")
        if job_id is not None:
            self.server.manager.check_job_id(job_id)
        op = find(match["op"])
        wait = _wait(query)
        params = params_from_json(op, self._decode_body(raw))
        params = self.server.guard.check(params)
        card = _flag(query, "card") if "card" in query else None
        job = self.server.manager.submit(
            op,
            op.request_values(params),
            card=card,
            job_id=job_id,
        )
        if wait:
            job = self.server.manager.wait(job.id, wait) or job
        self._send_json(200 if job.done else 202, job_view(job, query))

    def _job(self, match: re.Match[str]) -> Job:
        job = self.server.manager.get(match["id"])
        if job is None:
            raise RequestError(404, "unknown_job", f"No job {match['id']!r}.")
        return job

    def list_jobs(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        status = query.get("status", [None])[-1]
        self._send_json(200, Jobs(jobs=self.server.manager.summaries(status)))

    def get_job(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        job = self._job(match)
        wait = _wait(query)
        if wait:
            job = self.server.manager.wait(job.id, wait) or job
        self._send_json(200, job_view(job, query))

    def cancel_job(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        job = self._job(match)
        if job.done:
            raise RequestError(
                409, "finished", f"Job {job.id} already finished ({job.status})."
            )
        job = self.server.manager.cancel(job.id) or job
        self._send_json(200 if job.done else 202, job_view(job, query))

    def rerun_job(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        job = self._job(match)
        op = find(job.op)
        params = self.server.guard.check(params_from_json(op, job.params))
        new = self.server.manager.submit(op, op.request_values(params), card=job.card)
        self._send_json(202, job_view(new, query))

    def job_log(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        job = self._job(match)
        try:
            tail = int(query.get("tail", ["200"])[-1])
            if tail < 0:
                raise ValueError
        except ValueError:
            raise RequestError(
                400, "usage", "tail must be a nonnegative integer."
            ) from None
        with read_job_log(self.server.manager.log_path(job.id), tail) as chunks:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(chunk)

    def job_events(self, match: re.Match[str], query: dict[str, list[str]]) -> None:
        """Server-sent events: each event's ``id`` is its ``seq``; the stream
        ends after the job's final status. ``Last-Event-ID`` resumes."""
        job = self._job(match)
        try:
            after = int(self.headers.get("Last-Event-ID") or 0)
        except ValueError:
            after = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        while True:
            events, finished = self.server.manager.events(
                job, after, SSE_KEEPALIVE_SECONDS
            )
            for event in events:
                after = event["seq"]
                data = json.dumps(event, separators=(",", ":"))
                self.wfile.write(
                    f"id: {after}\nevent: {event['type']}\ndata: {data}\n\n".encode()
                )
            if not events:
                self.wfile.write(b": keepalive\n\n")
            self.wfile.flush()
            # The final status event is recorded together with the job finishing.
            if finished:
                return


_OP = r"(?P<op>[a-z0-9-]+(?:/[a-z0-9-]+)*)"
_ID = r"(?P<id>[A-Za-z0-9-]+)"
ROUTES: list[tuple[str, re.Pattern[str], Route]] = [
    (method, re.compile(pattern), route)
    for method, pattern, route in (
        ("GET", "/v1/health", Handler.health),
        ("GET", "/v1/viewer-file", Handler.viewer_file),
        ("HEAD", "/v1/viewer-file", Handler.viewer_file),
        ("GET", "/v1/?", Handler.index),
        ("GET", "/v1/openapi.json", Handler.openapi),
        ("GET", "/v1/ops", Handler.list_ops),
        ("GET", f"/v1/ops/{_OP}", Handler.describe_op),
        ("POST", f"/v1/ops/{_OP}", Handler.run_op),
        ("GET", "/v1/jobs", Handler.list_jobs),
        ("GET", f"/v1/jobs/{_ID}", Handler.get_job),
        ("DELETE", f"/v1/jobs/{_ID}", Handler.cancel_job),
        ("POST", f"/v1/jobs/{_ID}/rerun", Handler.rerun_job),
        ("GET", f"/v1/jobs/{_ID}/log", Handler.job_log),
        ("GET", f"/v1/jobs/{_ID}/events", Handler.job_events),
        ("GET", "/v1/(?P<route>manifest|variants|report)", Handler.lookup),
    )
]


# OpenAPI ---------------------------------------------------------------------


def _response(description: str, schema: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}
        },
    }


def _errors(*codes: int) -> dict[str, Any]:
    # Python 3.13 renamed the HTTP 413 phrase. Keep the published OpenAPI
    # description identical across the Python versions that build or serve it.
    return {
        str(code): _response(
            "Content Too Large" if code == 413 else HTTPStatus(code).phrase, "Error"
        )
        for code in codes
    }


_WAIT = {
    "name": "wait",
    "in": "query",
    "description": "Wait up to this many seconds for the job to finish.",
    "schema": {"type": "number", "minimum": 0, "maximum": MAX_WAIT_SECONDS},
}
_FULL = {
    "name": "full",
    "in": "query",
    "description": "Include the params and the result's provenance.",
    "schema": {"type": "boolean"},
}
_CARD = {
    "name": "card",
    "in": "query",
    "description": "Write this job's result card (<name>.card.html), or not; "
    "without it, as `serve --card` or result_card says.",
    "schema": {"type": "boolean"},
}
_JOB_ID = {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}


_DELETES = (
    " It can delete data it didn't make, so the server refuses it (403 "
    "destructive) unless started with `serve --allow-destructive`."
)


def openapi_document(
    server_url: str = "http://127.0.0.1", *, health_requires_auth: bool = False
) -> dict[str, Any]:
    """The OpenAPI 3.1 description of the HTTP API, generated from the API's
    Params classes."""
    from .transport_schema import response_schemas
    from .viewer_http import openapi_path

    schemas: dict[str, Any] = response_schemas()
    paths: dict[str, Any] = {}
    paths["/v1/viewer-file"] = openapi_path()
    for op in operations().values():
        name = op.params.__name__
        schemas[name] = op.schema()
        paths[f"/v1/ops/{op.name}"] = {
            "get": {
                "operationId": f"describe_{op.tool_name}",
                "summary": f"Describe {op.command}",
                "responses": {
                    "200": _response(
                        "The operation and its params", "OperationDescription"
                    )
                },
            },
            "post": {
                "operationId": op.tool_name,
                "summary": op.summary,
                "description": f"Runs `wgsextract {op.command}` as a {op.kind}."
                + (_DELETES if op.may_delete else ""),
                "parameters": [
                    _WAIT,
                    _FULL,
                    _CARD,
                    {
                        "name": "X-WGSE-Job-ID",
                        "in": "header",
                        "description": "Client-generated lowercase UUIDv4. Use GET /v1/jobs/{id} "
                        "to recover the original job after losing the response.",
                        "schema": {"type": "string", "format": "uuid"},
                    },
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": f"#/components/schemas/{name}"}
                        }
                    },
                },
                "responses": {
                    "200": _response("Finished within ?wait", "Job"),
                    "202": _response("Queued or running", "Job"),
                    **_errors(400, 401, 403, 404, 409, 411, 413, 415),
                },
            },
        }
    for lookup in QUERIES.values():
        paths[f"/v1/{lookup.route}"] = {
            "get": {
                "operationId": lookup.name,
                "summary": lookup.summary,
                "description": "Read-only; array parameters are comma-separated.",
                "parameters": [
                    {
                        "name": name,
                        "in": "query",
                        "required": name in lookup.required,
                        "schema": spec,
                        **({"explode": False} if spec["type"] == "array" else {}),
                    }
                    for name, spec in lookup.properties.items()
                ],
                "responses": {
                    "200": (
                        _response("The answer", "VariantPage")
                        if lookup.name == "query_variants"
                        else {
                            "description": "The loaded document (not fully validated)",
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            },
                        }
                    ),
                    **_errors(400, 401, 403, 404),
                },
            }
        }
    paths.update(
        {
            "/v1/jobs": {
                "get": {
                    "operationId": "list_jobs",
                    "summary": "List jobs (id, op, status, times)",
                    "parameters": [
                        {"name": "status", "in": "query", "schema": {"type": "string"}}
                    ],
                    "responses": {"200": _response("The jobs", "Jobs")},
                }
            },
            "/v1/jobs/{id}": {
                "parameters": [_JOB_ID],
                "get": {
                    "operationId": "get_job",
                    "summary": "A job, with its result once finished",
                    "parameters": [_WAIT, _FULL],
                    "responses": {
                        "200": _response("The job", "Job"),
                        **_errors(401, 404),
                    },
                },
                "delete": {
                    "operationId": "cancel_job",
                    "summary": "Cancel a queued or running job",
                    "parameters": [_FULL],
                    "responses": {
                        "200": _response("Cancelled", "Job"),
                        "202": _response("Cancelling", "Job"),
                        **_errors(401, 404, 409),
                    },
                },
            },
            "/v1/jobs/{id}/rerun": {
                "parameters": [_JOB_ID],
                "post": {
                    "operationId": "rerun_job",
                    "summary": "Run a job's params again; long workflows resume "
                    "from their completed steps",
                    "parameters": [_FULL],
                    "responses": {
                        "202": _response("The new job", "Job"),
                        **_errors(401, 403, 404),
                    },
                },
            },
            "/v1/jobs/{id}/events": {
                "parameters": [_JOB_ID],
                "get": {
                    "operationId": "job_events",
                    "summary": "Progress as server-sent events (status, progress); "
                    "ends after the final status",
                    "responses": {
                        "200": {
                            "description": "An event stream",
                            "content": {
                                "text/event-stream": {
                                    "schema": {"type": "string"},
                                    "x-event-schema": {
                                        "$ref": "#/components/schemas/JobEvent"
                                    },
                                }
                            },
                        }
                    },
                },
            },
            "/v1/jobs/{id}/log": {
                "parameters": [
                    _JOB_ID,
                    {
                        "name": "tail",
                        "in": "query",
                        "description": "Number of lines to return; 0 returns the full log.",
                        "schema": {"type": "integer", "minimum": 0, "default": 200},
                    },
                ],
                "get": {
                    "operationId": "job_log",
                    "summary": "The last lines of the job's log",
                    "responses": {
                        "200": {
                            "description": "Log text",
                            "content": {"text/plain": {}},
                        },
                        **_errors(400, 401, 403, 404),
                    },
                },
            },
        }
    )
    for path, name, model in (
        ("/v1/health", "health", "Health"),
        ("/v1/", "index", "Index"),
        ("/v1/ops", "list_ops", "Operations"),
    ):
        paths[path] = {
            "get": {
                "operationId": name,
                "responses": {"200": _response("The answer", model)},
                **(
                    {"security": []}
                    if name == "health" and not health_requires_auth
                    else {}
                ),
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "WGS Extract",
            "version": __version__,
            "description": "The typed WGS Extract API (docs/api.md) over local "
            "HTTP: operations run as jobs. See docs/server.md.",
        },
        "servers": [{"url": server_url}],
        "security": [{"bearer": []}],
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        },
    }
