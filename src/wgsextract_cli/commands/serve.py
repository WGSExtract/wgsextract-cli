"""``wgsextract serve``: the typed API as a local HTTP/JSON server, or as an MCP
server on stdio or streamable HTTP (``--mcp``). See docs/server.md."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import shutil
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from wgsextract_cli.cli.registration.serve import register as register
from wgsextract_cli.core.config import dataset_library, get_config_path, settings
from wgsextract_cli.core.results import json_mode
from wgsextract_cli.core.utils import UsageError, WGSExtractError

if TYPE_CHECKING:
    from wgsextract_cli.server.jobs import JobManager
    from wgsextract_cli.server.operations import PathGuard


MCP_TRANSPORTS = ("stdio", "http")


def allowed_roots(extra: Iterable[str]) -> list[Path]:
    """The folders requests may use: ``extra`` and server_allowed_paths, which
    must exist, and the configured data folders that do (one not created or
    mounted yet doesn't stop the server)."""
    configured = settings.get("server_allowed_paths") or []
    if isinstance(configured, str):
        configured = [configured]
    roots = [Path(os.path.expanduser(root)) for root in (*extra, *configured)]
    for root in roots:
        if not root.is_dir():
            raise UsageError(f"Allowed folder {root} doesn't exist.")
    configured_roots = {
        "dataset_library": dataset_library(),
        "reference_library": settings.get("reference_library"),
        "reference_genome_library": settings.get("reference_genome_library"),
        "output_directory": settings.get("output_directory"),
    }
    for key, value in configured_roots.items():
        if not value:
            continue
        root = Path(os.path.expanduser(str(value)))
        if root.is_dir():
            roots.append(root)
        else:
            logging.warning(f"Not allowing {key} {root}: it doesn't exist.")
    return roots


def _setting_int(
    value: int | None, flag: str, key: str, low: int, high: int | None = None
) -> int:
    if value is None:
        value = int(settings.get(key) or low)
    if value < low or (high is not None and value > high):
        raise UsageError(f"{flag} (config {key}) is out of range: {value}.")
    return value


def run(args: argparse.Namespace) -> None:
    try:
        _run(args)
    except WGSExtractError as e:
        # The program waiting for the ready line learns why it won't come.
        if args.ready_json and not json_mode():
            import json

            print(json.dumps({"error": {"message": str(e)}}), flush=True)
        raise


def _run(args: argparse.Namespace) -> None:
    from wgsextract_cli.core.last_run import state_directory
    from wgsextract_cli.server.jobs import JobManager

    if args.openapi:
        import json

        from wgsextract_cli.server.http_api import openapi_document

        sys.stdout.write(json.dumps(openapi_document(), indent=1) + "\n")
        return
    transport = _mcp_transport(args)
    if args.allow_jobs and not transport:
        raise UsageError("--allow-jobs is for --mcp; HTTP clients may run any job.")
    if transport == "stdio" and (json_mode() or args.ready_json):
        flag = "--json" if json_mode() else "--ready-json"
        raise UsageError(f"--mcp uses stdout for the protocol; drop {flag}.")
    if args.ready_json and json_mode():
        raise UsageError("--ready-json and --json both use stdout; pick one.")
    if transport == "http":
        from wgsextract_cli.server.mcp_http import require_sdk

        require_sdk()
    port_key = "server_mcp_port" if transport == "http" else "server_port"
    port = _setting_int(args.port, "--port", port_key, 0, 65535)
    max_jobs = _setting_int(args.max_jobs, "--max-jobs", "server_max_jobs", 1)

    from wgsextract_cli.server.operations import PathGuard

    roots = allowed_roots(args.allow)
    guard = PathGuard(roots, allow_destructive=args.allow_destructive)
    if not guard.roots:
        logging.warning(
            "No folders are allowed, so requests can't name any file. Pass "
            "--allow DIR or set server_allowed_paths in config.toml."
        )
    state = state_directory() / "server"
    # The HTTP server keeps its jobs across restarts. Each MCP session (one per
    # agent conversation, several may run at once) has its own, removed when
    # it ends: the outputs and their manifests stay where the jobs saved them.
    jobs = (
        Path(tempfile.mkdtemp(prefix="mcp-", dir=_made(state)))
        if transport
        else state / "jobs"
    )
    manager = JobManager(
        jobs,
        config_path=get_config_path(),
        max_jobs=max_jobs,
        debug=bool(getattr(args, "debug", False)),
        default_outdir=settings.get("output_directory"),
        # As given: a worker allows a symlinked root as written, as here.
        allowed=roots,
    )
    try:
        if transport == "stdio":
            _serve_mcp(manager, guard, allow_jobs=args.allow_jobs)
        elif transport == "http":
            _serve_mcp_http(
                manager,
                guard,
                port,
                state / "mcp.json",
                allow_jobs=args.allow_jobs,
                ready_json=args.ready_json,
            )
        else:
            _serve_http(
                manager, guard, port, state / "server.json", ready_json=args.ready_json
            )
    finally:
        manager.shutdown()
        if transport:
            shutil.rmtree(jobs, ignore_errors=True)


def _mcp_transport(args: argparse.Namespace) -> str | None:
    """stdio or http for MCP, or None for the HTTP/JSON API."""
    if not (args.mcp or args.mcp_transport):
        return None
    transport = args.mcp_transport or settings.get("server_mcp_transport") or "stdio"
    if transport not in MCP_TRANSPORTS:
        raise UsageError(
            f"Config server_mcp_transport must be stdio or http, not {transport!r}."
        )
    return str(transport)


def _made(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _serve_mcp(manager: JobManager, guard: PathGuard, *, allow_jobs: bool) -> None:
    from wgsextract_cli.server.mcp_server import McpServer

    logging.info(
        "WGS Extract MCP server on stdio"
        + (" (jobs allowed)" if allow_jobs else " (read-only queries)")
    )
    server = McpServer(manager, guard, allow_jobs=allow_jobs, stdout=sys.stdout)
    try:
        server.serve(sys.stdin)
    except (KeyboardInterrupt, SystemExit):
        logging.info("Stopping the MCP server.")


def _serve_mcp_http(
    manager: JobManager,
    guard: PathGuard,
    port: int,
    discovery: Path,
    *,
    allow_jobs: bool,
    ready_json: bool,
) -> None:
    from wgsextract_cli.server.mcp_http import PATH, listen, serve
    from wgsextract_cli.server.mcp_server import McpTools

    token = secrets.token_urlsafe(32)
    try:
        sock = listen(port)
    except OSError as e:
        raise UsageError(f"Can't listen on 127.0.0.1:{port}: {e}") from None
    url = f"http://127.0.0.1:{sock.getsockname()[1]}{PATH}"
    mode = " (jobs allowed)" if allow_jobs else " (read-only queries)"
    _announce(
        f"WGS Extract MCP server (streamable HTTP) on {url}{mode}",
        url,
        token,
        discovery,
        guard,
        f"Clients send it as 'Authorization: Bearer {token}'.",
        ready_json=ready_json,
    )
    try:
        serve(McpTools(manager, guard, allow_jobs=allow_jobs), token, sock)
    except (KeyboardInterrupt, SystemExit):
        # uvicorn shuts down gracefully, then re-raises the signal.
        logging.info("Stopping the MCP server.")
    finally:
        sock.close()
        _remove_discovery(discovery)


def _serve_http(
    manager: JobManager,
    guard: PathGuard,
    port: int,
    discovery: Path,
    *,
    ready_json: bool,
) -> None:
    from wgsextract_cli.server.http_api import Server

    token = secrets.token_urlsafe(32)
    try:
        server = Server(port, token, manager, guard)
    except OSError as e:
        raise UsageError(f"Can't listen on 127.0.0.1:{port}: {e}") from None
    url = server.url + "/v1"
    _announce(
        f"WGS Extract server on {url}",
        url,
        token,
        discovery,
        guard,
        f"Try: curl -H 'Authorization: Bearer {token}' {url}/ops",
        ready_json=ready_json,
    )
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Stopping the server.")
    finally:
        server.server_close()
        _remove_discovery(discovery)


def _announce(
    title: str,
    url: str,
    token: str,
    discovery: Path,
    guard: PathGuard,
    hint: str,
    *,
    ready_json: bool,
) -> None:
    """Save the private discovery file and tell the user (on stderr) and, with
    --ready-json, the program that started the server (on stdout) where the
    server is."""
    import json

    from wgsextract_cli.server.jobs import write_private_json

    address = {"url": url, "token": token, "pid": os.getpid()}
    write_private_json(discovery, address)
    print(
        f"{title}\n"
        f"Token: {token}\n"
        f"  (also in {discovery}, readable by you only)\n"
        f"Allowed folders: {', '.join(map(str, guard.roots)) or 'none'}\n"
        f"{hint}\n"
        "Stop with Ctrl-C.",
        file=sys.stderr,
        flush=True,
    )
    if ready_json:
        print(json.dumps(address), flush=True)


def _remove_discovery(discovery: Path) -> None:
    """Remove the discovery file unless a newer server has replaced it."""
    import json

    try:
        if json.loads(discovery.read_text()).get("pid") == os.getpid():
            discovery.unlink()
    except (OSError, ValueError):
        pass
