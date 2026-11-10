"""BAM/CRAM inputs read over http(s) (#431).

htslib reads a remote alignment by byte range, so realign can align a
public whole-genome CRAM without a local copy. Only its index is downloaded,
beside the output, and given to samtools as ``URL##idx##INDEX``: htslib
would otherwise drop it into the current directory.

URLs are public ones: a query string, fragment or credentials (a signed
URL's token) is refused, so none reaches a manifest, a journal or a log, and
the URL's path names the file's format.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass
from functools import lru_cache
from http.client import HTTPException
from os import fspath
from pathlib import Path
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from wgsextract_cli.core.atomic_output import atomic_write
from wgsextract_cli.core.paths import PathInput
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command

_TIMEOUT = 60
_INDEX_SUFFIXES = {".cram": (".crai",), ".bam": (".bai", ".csi")}
# An index is a few MB even for a whole genome; more is the wrong file.
_MAX_INDEX_BYTES = 256 * 1024**2
_COPY_BUFFER = 8 * 1024 * 1024


def is_url(path: PathInput | None) -> bool:
    """Whether ``path`` is an http(s) URL."""
    if path is None:
        return False
    return urlsplit(fspath(path)).scheme.lower() in {"http", "https"}


def check_public(url: str) -> None:
    """Refuse a URL with a query, fragment or credentials (a signed URL's
    token), before anything records or reports it."""
    parts = urlsplit(url)
    if parts.query or parts.fragment or parts.username or parts.password:
        raise UsageError(
            "Give a public http(s) URL of the BAM/CRAM without a query string "
            "or credentials (signed URLs aren't supported)."
        )


def shown(url: str) -> str:
    """The URL without credentials, query or fragment, for records and
    messages."""
    parts = urlsplit(url)
    # The netloc as written (an IPv6 host keeps its brackets; a bad port
    # isn't parsed), minus any credentials.
    return urlunsplit(
        (parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", "")
    )


def check_url(url: str) -> None:
    """Refuse a URL realign can't use: not public (check_public), not
    naming a BAM/CRAM, or when samtools can't read http(s) (built without
    libcurl, as on Windows)."""
    check_public(url)
    if Path(urlsplit(url).path).suffix.lower() not in _INDEX_SUFFIXES:
        raise UsageError(f"{shown(url)} doesn't name a .bam or .cram file.")
    if not _samtools_reads_urls():
        raise WGSExtractError(
            "This samtools can't read http(s) URLs (its htslib was built "
            "without libcurl). Download the file and realign the local copy."
        )


@lru_cache(maxsize=1)
def _samtools_reads_urls() -> bool:
    version = run_command(["samtools", "--version"], capture_output=True).stdout
    return "libcurl=yes" in version


@dataclass(frozen=True)
class RemoteFile:
    """A remote file's size and a fingerprint that changes with its content:
    its ETag, else its size and modification time. Without either, every
    look gets a new fingerprint: the file is never assumed unchanged."""

    url: str
    size: int
    fingerprint: str


def remote_file(url: str) -> RemoteFile:
    """The remote file's size and fingerprint, from an HTTP HEAD request."""
    try:
        with urlopen(Request(url, method="HEAD"), timeout=_TIMEOUT) as response:
            headers = response.headers
    except (URLError, OSError, ValueError, HTTPException) as error:
        raise WGSExtractError(f"Can't reach {shown(url)}: {error}") from error
    size = int(headers.get("Content-Length") or 0)
    validator = headers.get("ETag") or headers.get("Last-Modified")
    if validator:
        identity = f"{url}\n{size}\n{validator}"
    else:
        logging.warning(
            "%s has no ETag or Last-Modified: a rerun can't tell whether it "
            "changed, so it won't reuse work done on it.",
            shown(url),
        )
        identity = uuid.uuid4().hex
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return RemoteFile(shown(url), size, f"url:{digest}")


def input_size(path: str) -> int:
    """The bytes of a local or remote input."""
    return remote_file(path).size if is_url(path) else Path(path).stat().st_size


def index_files(url: str, directory: PathInput) -> list[Path]:
    """Where fetch_index may save the URL's index in ``directory``."""
    path = Path(urlsplit(url).path)
    suffixes = _INDEX_SUFFIXES.get(path.suffix.lower(), ())
    return [Path(directory) / f".{path.name}{suffix}" for suffix in suffixes]


def fetch_index(url: str, directory: PathInput) -> str | None:
    """Download the remote alignment's index (``URL.crai``, ``.bai`` or
    ``.csi``) into ``directory``; None when the server has none (404, or
    S3's 403). Other failures raise: a rerun can still use the index."""
    for local in index_files(url, directory):
        address = url + local.name.removeprefix("." + Path(urlsplit(url).path).name)
        try:
            with urlopen(address, timeout=_TIMEOUT) as response:
                data = response.read(_MAX_INDEX_BYTES + 1)
        except HTTPError as error:
            if error.code in (403, 404):
                logging.debug("No index at %s: %s", address, error)
                continue
            raise WGSExtractError(f"Can't download {address}: {error}") from error
        except (URLError, OSError, ValueError, HTTPException) as error:
            raise WGSExtractError(f"Can't download {address}: {error}") from error
        if len(data) > _MAX_INDEX_BYTES:
            raise WGSExtractError(f"{address} is too large to be an index.")
        atomic_write(local, data)
        return str(local)
    return None


def copy_range(path: str, start: int, end: int | None, output: BinaryIO) -> None:
    """Copy bytes ``start`` to ``end`` (None: to the end) of a local file or
    a URL (by an HTTP range request) into ``output``."""
    if is_url(path):
        span = f"bytes={start}-{'' if end is None else end - 1}"
        try:
            with urlopen(
                Request(path, headers={"Range": span}), timeout=_TIMEOUT
            ) as response:
                if response.status != 206:
                    raise WGSExtractError(
                        f"{shown(path)} ignored a range request (HTTP "
                        f"{response.status})."
                    )
                shutil.copyfileobj(response, output, _COPY_BUFFER)
        except (URLError, OSError, ValueError, HTTPException) as error:
            raise WGSExtractError(f"Can't read {shown(path)}: {error}") from error
        return
    with open(path, "rb") as handle:
        handle.seek(start)
        remaining = None if end is None else end - start
        while remaining is None or remaining > 0:
            block = handle.read(
                _COPY_BUFFER if remaining is None else min(_COPY_BUFFER, remaining)
            )
            if not block:
                break
            output.write(block)
            if remaining is not None:
                remaining -= len(block)


def with_index(url: str, index: str) -> str:
    """The URL with its local index, as htslib opens it."""
    return f"{url}##idx##{index}"
