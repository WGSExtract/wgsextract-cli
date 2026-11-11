"""wgse-bwa's optional dense suffix-array side file (native/README.md).

``wgse-bwa densify-sa`` writes ``<ref>.wgse-sa<interval>`` beside a BWA index:
the suffix-array position of every ``interval``-th row (0.8 GB for hs38 at
16). ``wgse-bwa mem`` then locates seeds with shorter walks, with identical
alignments, and holds the file in RAM. WGS Extract never builds or uses it
unless asked: ``ref dense-sa`` builds it, and ``align``/``realign`` read it
only with ``--dense-suffix-array`` or config ``dense_suffix_array = true``;
otherwise they tell wgse-bwa ``--dense-sa off``.
"""

from __future__ import annotations

import json
import math
import re
import struct
import subprocess
from functools import lru_cache
from pathlib import Path

from wgsextract_cli.core.native_aligners import (
    DENSE_SA_VERSION,
    INSTALL_HINT,
    NativeAligner,
    NativeAlignerUnusable,
    installed_build,
)
from wgsextract_cli.core.results import current_result, warn

DEFAULT_INTERVAL = 16
# Powers of two below BWA's default sample interval (32).
INTERVALS = (2, 4, 8, 16)
_HEADER_BYTES = 104
_NAME = re.compile(r"\.wgse-sa(\d+)")
_CAPABILITIES_TIMEOUT_SECONDS = 120
UNUSED = "dense_suffix_array_unused"
_MINIMUM = ".".join(map(str, DENSE_SA_VERSION))


def side_file(ref: str, interval: int = DEFAULT_INTERVAL) -> Path:
    """Where ``densify-sa`` writes the file of ``interval`` for ``ref``'s index."""
    return Path(f"{ref}.wgse-sa{interval}")


def side_files(ref: str) -> list[Path]:
    """``ref``'s side files, densest first."""
    found = []
    name = Path(ref).name
    for path in Path(ref).parent.glob(f"{name}.wgse-sa*"):
        match = _NAME.fullmatch(path.name.removeprefix(name))
        if match and path.is_file():
            found.append((int(match[1]), path))
    return [path for _, path in sorted(found)]


def interval_of(path: Path) -> int:
    match = re.search(r"\.wgse-sa(\d+)$", path.name)
    if match is None:
        raise ValueError(f"{path} is not a dense suffix-array file")
    return int(match[1])


def index_sampling(ref: str) -> tuple[int, int] | None:
    """The BWA index's suffix-array sample interval and sequence length
    (from the ``.sa`` header), or None without a readable index."""
    try:
        with open(f"{ref}.sa", "rb") as handle:
            header = handle.read(7 * 8)
    except OSError:
        return None
    if len(header) < 7 * 8:
        return None
    values = struct.unpack("<7Q", header)
    return values[5], values[6]


def planned_bytes(ref: str, interval: int) -> int | None:
    """The size of the file ``densify-sa -i interval`` writes for ``ref``, or
    None without its BWA index or when the index can't be made denser so."""
    sampling = index_sampling(ref)
    if sampling is None:
        return None
    sa_interval, length = sampling
    if not sa_interval or interval >= sa_interval or sa_interval % interval:
        return None
    entries = (length // sa_interval + 1) * (sa_interval // interval - 1)
    return _HEADER_BYTES + 4 * entries + 4 * math.ceil(entries / 32)


def build() -> NativeAligner:
    """The wgse-bwa on PATH that reads and writes side files."""
    try:
        native = installed_build("bwa")
    except NativeAlignerUnusable as error:
        raise NativeAlignerUnusable(f"wgse-bwa {error} ({INSTALL_HINT})") from error
    if not native.reads_dense_sa:
        raise NativeAlignerUnusable(
            f"wgse-bwa {native.version} at {native.path} predates dense "
            f"suffix-array files; they need {'.'.join(map(str, DENSE_SA_VERSION))} or "
            f"later ({INSTALL_HINT})"
        )
    return native


def readable_interval(native: NativeAligner, ref: str) -> int | None:
    """The interval of the side file ``native``'s ``mem`` would read for
    ``ref`` (``capabilities --json <index>``: present, intact, made from this
    index and fitting this computer's RAM), or None. Asked once per process
    while the files are unchanged (it checksums them)."""
    files = tuple(
        (str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in side_files(ref)
    )
    if not files:
        return None
    build = Path(native.path).stat()
    return _readable_interval(
        native.path, ref, (*files, (native.path, build.st_mtime_ns, build.st_size))
    )


@lru_cache(maxsize=16)
def _readable_interval(
    path: str, ref: str, files: tuple[tuple[str, int, int], ...]
) -> int | None:
    del files
    try:
        completed = subprocess.run(
            [path, "capabilities", "--json", ref],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=_CAPABILITIES_TIMEOUT_SECONDS,
        )
        document = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    dense = document.get("dense_sa") if isinstance(document, dict) else None
    interval = dense.get("interval") if isinstance(dense, dict) else None
    return interval if isinstance(interval, int) else None


def _warn_once(reason: str, message: str, **details: str | int) -> None:
    if any(w.code == UNUSED for w in current_result().warnings):
        return
    warn(UNUSED, message, reason=reason, **details)


def for_alignment(
    native: NativeAligner | None, ref: str, *, enabled: bool
) -> Path | None:
    """The side file wgse-bwa reads for this alignment of ``ref`` with bwa,
    or None. When ``enabled`` but it can't be used (no wgse-bwa, too old, no
    file, or wgse-bwa won't read it), warn (dense_suffix_array_unused) and
    align without it."""
    if not enabled:
        return None
    command = f"wgsextract ref dense-sa --ref {ref}"
    if native is None:
        _warn_once(
            "no_wgse_bwa",
            "Not using a dense suffix-array file: only wgse-bwa on Apple "
            f"Silicon reads one, and it isn't aligning ({INSTALL_HINT}).",
        )
        return None
    if not native.reads_dense_sa:
        _warn_once(
            "wgse_bwa_too_old",
            f"Not using a dense suffix-array file: wgse-bwa {native.version} "
            f"predates them; they need {'.'.join(map(str, DENSE_SA_VERSION))} or later "
            f"({INSTALL_HINT}).",
            version=native.version,
        )
        return None
    files = side_files(ref)
    if not files:
        _warn_once(
            "missing",
            f"Not using a dense suffix-array file: {ref} has none; `{command}` "
            "builds one.",
            path=str(side_file(ref)),
        )
        return None
    interval = readable_interval(native, ref)
    chosen = next((p for p in files if interval_of(p) == interval), None)
    if chosen is None:
        _warn_once(
            "unusable",
            f"Not using {files[0]}: wgse-bwa won't read it (it needs 16 GiB of "
            "RAM or more and takes at most an eighth of it, and it must be "
            f"intact and made from this index; `{command}` rebuilds it).",
            path=str(files[0]),
        )
        return None
    return chosen
