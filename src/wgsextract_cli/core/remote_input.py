"""BAM/CRAM inputs read over http(s) (#431).

htslib reads a remote alignment by byte range, so realign can align a
public whole-genome CRAM without a local copy. Only its index is downloaded,
beside the output, and given to samtools as ``URL##idx##INDEX``: htslib
would otherwise drop it into the current directory.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from os import fspath
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from wgsextract_cli.core.atomic_output import atomic_write
from wgsextract_cli.core.paths import PathInput
from wgsextract_cli.core.utils import WGSExtractError

_TIMEOUT = 60
_INDEX_SUFFIXES = {".cram": (".crai",), ".bam": (".bai", ".csi")}


def is_url(path: PathInput | None) -> bool:
    """Whether ``path`` is an http(s) URL."""
    if path is None:
        return False
    return urlsplit(fspath(path)).scheme.lower() in {"http", "https"}


@dataclass(frozen=True)
class RemoteFile:
    """A remote file's size and a fingerprint that changes with its content
    (its ETag, else its size and modification time)."""

    url: str
    size: int
    fingerprint: str


def remote_file(url: str) -> RemoteFile:
    """The remote file's size and fingerprint, from an HTTP HEAD request."""
    try:
        with urlopen(Request(url, method="HEAD"), timeout=_TIMEOUT) as response:
            headers = response.headers
    except (URLError, OSError, ValueError) as error:
        raise WGSExtractError(f"Can't reach {_shown(url)}: {error}") from error
    size = int(headers.get("Content-Length") or 0)
    identity = headers.get("ETag") or f"{size}:{headers.get('Last-Modified', '')}"
    digest = hashlib.sha256(f"{url}\n{identity}".encode()).hexdigest()
    return RemoteFile(url, size, f"url:{digest}")


def input_size(path: str) -> int:
    """The bytes of a local or remote input."""
    return remote_file(path).size if is_url(path) else Path(path).stat().st_size


def fetch_index(url: str, directory: PathInput) -> str | None:
    """Download the remote alignment's index (``URL.crai``, ``.bai`` or
    ``.csi``) into ``directory``; None when it has none."""
    suffix = Path(urlsplit(url).path).suffix.lower()
    name = Path(urlsplit(url).path).name
    for index in _INDEX_SUFFIXES.get(suffix, ()):
        try:
            with urlopen(url + index, timeout=_TIMEOUT) as response:
                data = response.read()
        except (URLError, OSError, ValueError) as error:
            logging.debug("No index at %s%s: %s", _shown(url), index, error)
            continue
        local = Path(directory) / f".{name}{index}"
        atomic_write(local, data)
        return str(local)
    return None


def with_index(url: str, index: str) -> str:
    """The URL with its local index, as htslib opens it."""
    return f"{url}##idx##{index}"


def _shown(url: str) -> str:
    """The URL without query or credentials, for messages."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname or ''}{parts.path}"
