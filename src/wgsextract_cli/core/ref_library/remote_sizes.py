"""Download sizes of catalog genomes, probed with HTTP ``HEAD`` or FTP ``SIZE``
and cached in the library's ``.remote-sizes.json`` (``{url: {"bytes",
"fetched_at"}}``) for 30 days.

Probing is opt-in, concurrent and bounded: each request times out after
``PROBE_TIMEOUT`` seconds and the refresh stops waiting after
``BUDGET_SECONDS``, so a slow or offline network delays a listing by seconds at
most. A size that can't be found is left out, never guessed.
"""

from __future__ import annotations

import ftplib
import json
import logging
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from wgsextract_cli.core.atomic_output import atomic_write

from .segmented import user_agent

CACHE_FILE = ".remote-sizes.json"
TTL_SECONDS = 30 * 24 * 3600
PROBE_TIMEOUT = 4.0
BUDGET_SECONDS = 8.0
WORKERS = 8


def _load(library: Path) -> dict[str, dict[str, float]]:
    try:
        data = json.loads((library / CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        url: entry
        for url, entry in data.items()
        if isinstance(entry, dict)
        and isinstance(entry.get("bytes"), int)
        and entry["bytes"] > 0
        and isinstance(entry.get("fetched_at"), int | float)
    }


def cached_sizes(library: Path) -> dict[str, int]:
    """Every size in the library's cache, however old."""
    return {url: int(entry["bytes"]) for url, entry in _load(library).items()}


def _ftp_size(url: str) -> int | None:
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.path:
        return None
    with ftplib.FTP(timeout=PROBE_TIMEOUT) as ftp:
        ftp.connect(parsed.hostname, parsed.port or 21)
        ftp.login()
        ftp.voidcmd("TYPE I")  # SIZE is defined for binary transfers
        return ftp.size(parsed.path)


def probe_size(url: str) -> int | None:
    """The size of the file at ``url``, or None when the server doesn't say."""
    scheme = urlparse(url).scheme
    try:
        if scheme == "ftp":
            return _ftp_size(url)
        if scheme not in ("http", "https"):
            return None
        request = Request(url, method="HEAD", headers={"User-Agent": user_agent(url)})
        with urlopen(request, timeout=PROBE_TIMEOUT) as response:
            length = response.headers.get("Content-Length")
    except (ftplib.Error, OSError, EOFError, HTTPException, ValueError) as e:
        logging.debug("No size for %s: %s", url, e)
        return None
    return int(length) if length and length.isdigit() else None


def refresh_sizes(library: Path, urls: Iterable[str]) -> dict[str, int]:
    """Probe the ``urls`` whose cached size is missing or stale, save what was
    found, and return every cached size."""
    cache = _load(library)
    now = time.time()
    stale = sorted(
        {
            url
            for url in urls
            if url and now - cache.get(url, {}).get("fetched_at", 0) >= TTL_SECONDS
        }
    )
    if not stale:
        return cached_sizes(library)
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    futures = {pool.submit(probe_size, url): url for url in stale}
    done, _ = wait(futures, timeout=BUDGET_SECONDS)
    # Requests still running end at their own timeout; nobody waits for them.
    pool.shutdown(wait=False, cancel_futures=True)
    found = {futures[f]: size for f in done if (size := f.result())}
    logging.info("Found the download size of %d of %d genomes.", len(found), len(stale))
    # A library that doesn't exist yet holds no genomes to cache sizes for.
    if found and library.is_dir():
        fetched = time.time()
        for url, size in found.items():
            cache[url] = {"bytes": size, "fetched_at": fetched}
        try:
            atomic_write(
                library / CACHE_FILE,
                json.dumps(cache, separators=(",", ":"), sort_keys=True).encode(),
            )
        except OSError as e:
            logging.warning("Could not save download sizes in %s: %s", library, e)
    return {url: int(entry["bytes"]) for url, entry in cache.items()}
