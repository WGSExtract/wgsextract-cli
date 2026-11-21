"""Large downloads over several connections.

Some servers cap each connection: Ensembl serves its 27.6 GB VEP cache at about
0.4 MB/s per connection, 19 hours over one. A file of at least
SEGMENTED_MIN_SIZE whose server takes byte ranges is fetched in SEGMENT_SIZE
pieces, CONNECTIONS at a time, straight into dest.partial. dest.partial.segments
records the source URL's digest and how much of each piece has arrived, so an
interrupted download resumes every piece where it stopped.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, Future, wait
from http.client import HTTPException, IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from wgsextract_cli.core.download_progress import (
    CancelEvent,
    DownloadCancelled,
    DownloadIncomplete,
    PercentProgressLogger,
    ProgressCallback,
    redact_urls,
    retryable_http_status,
)
from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor

CONNECTIONS = 8
SEGMENTED_MIN_SIZE = 1024**3
SEGMENT_SIZE = 256 * 1024**2
ATTEMPTS = 5
TIMEOUT_SECONDS = 60
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)
# Hosts that refuse an outdated browser's User-Agent (403) but serve a tool's.
_TOOL_AGENT_HOSTS = ("zenodo.org",)
# Hosts that refuse both but serve Python's own (REVEL's, checked 2026-11-16).
_PYTHON_AGENT_HOSTS = ("rothsj06.dmz.hpc.mssm.edu",)


def user_agent(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()

    def listed(hosts: tuple[str, ...]) -> bool:
        return any(host == h or host.endswith(f".{h}") for h in hosts)

    if listed(_TOOL_AGENT_HOSTS):
        return "wgsextract-cli"
    if listed(_PYTHON_AGENT_HOSTS):
        return f"Python-urllib/{sys.version_info.major}.{sys.version_info.minor}"
    return USER_AGENT


_CHUNK = 1024**2
_RESUMABLE = (DownloadIncomplete, IncompleteRead, TimeoutError, ConnectionError)


def _backoff(attempt: int) -> float:
    """Seconds to wait before a piece's next attempt."""
    return float(min(30, 2**attempt))


def _transient(error: Exception) -> bool:
    """A failure worth retrying the piece for: a dropped or refused connection,
    or a server that is busy (429, 5xx), not one that refuses the file (4xx)."""
    if isinstance(error, HTTPError):
        return retryable_http_status(error.code)
    return isinstance(error, (*_RESUMABLE, URLError))


class RangesUnsupported(Exception):
    """The server answered a byte-range request with the whole file."""


def ranged_size(url: str) -> int | None:
    """The file's size, if its server says it takes byte ranges."""
    try:
        request = Request(url, method="HEAD", headers={"User-Agent": user_agent(url)})
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            headers = response.headers
    except (OSError, HTTPException):
        return None
    length = headers.get("Content-Length") or ""
    if headers.get("Accept-Ranges", "").lower() != "bytes" or not length.isdigit():
        return None
    return int(length)


def _load_state(
    path: str, url_digest: str, expected_size: int | None = None
) -> tuple[int, dict[str, int]] | None:
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(state, dict) or state.get("url_sha256") != url_digest:
        return None
    size = state.get("size")
    if type(size) is not int or size <= 0 or expected_size not in (None, size):
        return None
    received = state.get("received")
    if (
        not isinstance(received, dict)
        or len(received) != (size - 1) // SEGMENT_SIZE + 1
    ):
        return None
    counts = {}
    for start in range(0, size, SEGMENT_SIZE):
        key = str(start)
        count = received.get(key)
        if type(count) is not int or not 0 <= count <= min(SEGMENT_SIZE, size - start):
            return None
        counts[key] = count
    return size, counts


def saved_size(state_path: str, url: str) -> int | None:
    """The size an unfinished segmented download of url recorded, if any."""
    state = _load_state(state_path, hashlib.sha256(url.encode()).hexdigest())
    return state[0] if state is not None else None


def download_segmented(
    url: str,
    dest: str,
    size: int,
    progress_callback: ProgressCallback | None = None,
    cancel_event: CancelEvent | None = None,
    *,
    verify: Callable[[str], bool] | None = None,
) -> bool:
    """Fetch into dest.partial, then verify and atomically replace dest.

    Interruptions retain partial/state; failed verification discards them.
    Raises RangesUnsupported if the server ignores byte ranges after all.
    """
    partial = dest + ".partial"
    state_path = partial + ".segments"
    url_digest = hashlib.sha256(url.encode()).hexdigest()
    location = redact_urls(url)
    starts = list(range(0, size, SEGMENT_SIZE))
    try:
        partial_size = Path(partial).stat().st_size
    except FileNotFoundError:
        partial_size = None
    state = _load_state(state_path, url_digest, size) if partial_size == size else None
    if state is None:
        Path(state_path).unlink(missing_ok=True)
        with open(partial, "wb") as f:
            f.truncate(size)
        received = {str(start): 0 for start in starts}
    else:
        _, received = state
    lock = threading.Lock()
    stop = threading.Event()

    def save_state() -> None:
        with lock:
            snapshot = dict(received)
        tmp = state_path + ".tmp"
        with open(tmp, "w", newline="\n") as f:
            json.dump({"url_sha256": url_digest, "size": size, "received": snapshot}, f)
        Path(tmp).replace(state_path)

    def fetch(start: int) -> None:
        end = min(start + SEGMENT_SIZE, size)
        for attempt in range(1, ATTEMPTS + 1):
            pos = start + received[str(start)]
            if pos >= end:
                return
            try:
                request = Request(
                    url,
                    headers={
                        "User-Agent": user_agent(url),
                        "Range": f"bytes={pos}-{end - 1}",
                    },
                )
                with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    if response.getcode() != 206:
                        raise RangesUnsupported(location)
                    with open(partial, "r+b") as f:
                        f.seek(pos)
                        while pos < end:
                            if stop.is_set():
                                return
                            chunk = response.read(min(_CHUNK, end - pos))
                            if not chunk:
                                raise DownloadIncomplete(
                                    f"bytes {pos}-{end - 1}: connection closed"
                                )
                            f.write(chunk)
                            # The state may be saved, then the process killed,
                            # before close: count only what reached the file.
                            f.flush()
                            pos += len(chunk)
                            with lock:
                                received[str(start)] = pos - start
                return
            except (HTTPException, OSError, ValueError) as e:
                if isinstance(e, HTTPError):
                    e.close()
                    detail = f"HTTP {e.code}"
                else:
                    detail = redact_urls(str(e), sources=(url,))
                if attempt == ATTEMPTS or not _transient(e):
                    raise OSError(f"{location}: {detail}") from None
                logging.debug(
                    "Retrying %s bytes %s-%s: %s", location, pos, end - 1, detail
                )
                # Cancelled, or another piece failed: stop backing off.
                if stop.wait(_backoff(attempt)):
                    return

    label = Path(dest).name
    report = progress_callback or PercentProgressLogger(label)
    logging.info(f"Downloading {label} over {CONNECTIONS} connections...")
    start_time = time.monotonic()
    already = sum(received.values())
    with ContextThreadPoolExecutor(CONNECTIONS) as pool:
        futures: set[Future[None]] = {
            pool.submit(fetch, start)
            for start in starts
            if received[str(start)] < min(SEGMENT_SIZE, size - start)
        }
        try:
            pending = futures
            while pending:
                done, pending = wait(pending, timeout=2, return_when=FIRST_EXCEPTION)
                for future in done:
                    future.result()  # raises the piece's error
                if cancel_event and cancel_event.is_set():
                    raise DownloadCancelled("Download cancelled by user.")
                with lock:
                    total = sum(received.values())
                elapsed = time.monotonic() - start_time
                report(total, size, (total - already) / elapsed if elapsed else 0.0)
                if pending:
                    save_state()
        except BaseException:
            stop.set()
            pool.shutdown(wait=True, cancel_futures=True)
            try:
                save_state()
            except OSError as error:
                logging.warning(
                    "Could not save download progress for %s: %s", label, error
                )
            raise
    save_state()

    if sum(received.values()) != size:
        raise DownloadIncomplete(f"{label}: {sum(received.values())} of {size} bytes")
    if verify is not None and not verify(partial):
        Path(partial).unlink(missing_ok=True)
        Path(state_path).unlink(missing_ok=True)
        return False
    Path(partial).replace(dest)
    Path(state_path).unlink()
    return True
