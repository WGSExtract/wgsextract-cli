"""Keep CRAM reference lookups on this computer (#636).

A tool that decodes a CRAM without being given the reference looks it up by
the MD5 in the CRAM header: in ``REF_CACHE``, then in each ``REF_PATH``
location, then at the ``@SQ UR:`` path. With ``REF_PATH`` unset, htslib before
1.22 (bundled by several tools we run, and by DeepVariant's and Clair3's
builds) searches EBI's reference server, which sends the reference's MD5 over
the network and fails offline.

While a command runs, the tools it starts get a ``REF_PATH`` with only local
locations: the user's own, with any http, https or ftp entry dropped, else
htslib's default cache directory (``hts-ref`` in the user's cache folder). A
reference that isn't found fails with htslib's usual error instead. Remote
entries are dropped rather than kept after the local ones because a miss would
still reach the server. htsjdk (GATK, FastQC) downloads only when the
``samjdk.use_cram_ref_download`` Java property is set, which we never set.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

REF_PATH = "REF_PATH"

# How htslib's tokenise_search_path recognises a URL entry: an optional '|',
# an optional 'URL=', a scheme, then '//host[:port]' whose colons don't split.
_URL_START = re.compile(r"\|?(?:URL=)?(?:https?|ftp):{1,2}(?://)?[^:/]*[:/]?:?")
_REMOTE = re.compile(r"\|?(?:URL=|https?:|ftp:)")


def _separator() -> str:
    return ";" if os.name == "nt" else ":"


def _entries(value: str, sep: str) -> list[str]:
    """``value``'s locations, split as htslib splits ``REF_PATH``."""
    entries: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(value):
        if value.startswith("::", i):
            current.append(":")
            i += 2
            continue
        if sep == ":" and not current and (url := _URL_START.match(value, i)):
            current.append(url.group())
            i = url.end()
            continue
        if value[i] == sep:
            if current:
                entries.append("".join(current))
            current = []
        else:
            current.append(value[i])
        i += 1
    if current:
        entries.append("".join(current))
    return entries


def _default_cache() -> str:
    """htslib's own default cache location, ``hts-ref`` in the cache folder."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return str(Path(base, "hts-ref")) + "/%2s/%2s/%s"


def local_ref_path(value: str | None) -> str:
    """``value`` (a ``REF_PATH``) without remote locations; htslib's default
    cache when none is left, never empty (empty means EBI to older htslib)."""
    sep = _separator()
    local = [entry for entry in _entries(value or "", sep) if not _REMOTE.match(entry)]
    if not local:
        local = [_default_cache()]
    if sep == ":":
        local = [entry.replace(":", "::") for entry in local]
    return sep.join(local)


@contextmanager
def local_reference_lookup() -> Iterator[None]:
    """Give every tool started meanwhile a local-only ``REF_PATH``."""
    previous = os.environ.get(REF_PATH)
    os.environ[REF_PATH] = local_ref_path(previous)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(REF_PATH, None)
        else:
            os.environ[REF_PATH] = previous
