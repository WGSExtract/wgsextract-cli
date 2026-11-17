"""Audited, byte-identical release mirrors for explicit resource downloads."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any

from wgsextract_cli.core.download_progress import CancelEvent, ProgressCallback

MANIFEST = Path(__file__).parents[1] / "assets" / "resource_mirrors.json"
MAX_ASSET_BYTES = 1_900_000_000


@cache
def resources() -> dict[str, dict[str, Any]]:
    """Load local pins only; inspecting the catalog never reaches the network."""
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {entry["upstream_url"]: entry for entry in data["resources"]}


def mirror_for(url: str) -> dict[str, Any] | None:
    entry = resources().get(url)
    if entry and entry["redistributable"] and entry.get("parts"):
        return entry
    return None


class MirrorError(OSError):
    """A mirror was unavailable or failed its integrity check."""


def mirror_chunks(
    entry: dict[str, Any],
    directory: Path,
    cancel_event: CancelEvent | None = None,
    progress_callback: ProgressCallback | None = None,
    use_cache: bool = True,
) -> Iterator[bytes]:
    """Verify each part before yielding; reject a corrupt concatenation too.

    Consumers must discard their output on error (a later part can fail).
    Only one release part, at most 1.9 GB, is staged at a time.
    """
    from wgsextract_cli.core.ref_library.downloads import download_file

    digest = hashlib.sha256()
    size = 0
    with tempfile.TemporaryDirectory(prefix="mirror-", dir=directory) as work:
        part = Path(work) / "part"
        for pinned in entry["parts"]:
            if cancel_event and cancel_event.is_set():
                raise MirrorError("download cancelled")

            def progress(
                current: int, total: int, speed: float, offset: int = size
            ) -> None:
                if progress_callback:
                    progress_callback(offset + current, entry["size"], speed)

            if not download_file(
                pinned["url"],
                str(part),
                progress if progress_callback else None,
                cancel_event,
                expected_sha256=pinned["sha256"],
                use_mirrors=False,
                use_cache=use_cache,
            ):
                raise MirrorError(f"could not verify {pinned['url']}")
            if part.stat().st_size != pinned["size"]:
                raise MirrorError(f"size mismatch for {pinned['url']}")
            with part.open("rb") as source:
                while chunk := source.read(1 << 20):
                    if cancel_event and cancel_event.is_set():
                        raise MirrorError("download cancelled")
                    digest.update(chunk)
                    size += len(chunk)
                    yield chunk
            part.unlink()
    if digest.hexdigest() != entry["sha256"] or size != entry["size"]:
        raise MirrorError("assembled resource checksum or size mismatch")


def download_mirror(
    entry: dict[str, Any],
    dest: str,
    progress_callback: ProgressCallback | None,
    cancel_event: CancelEvent | None,
    expected_sha256: str | None,
    use_cache: bool = True,
) -> bool:
    target = Path(dest)
    if expected_sha256 and expected_sha256.lower() != entry["sha256"]:
        logging.warning("MIRROR_FALLBACK: mirror differs from requested SHA-256.")
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="resource-", dir=target.parent) as work:
            staged = Path(work) / "assembled"
            with staged.open("wb") as output:
                for chunk in mirror_chunks(
                    entry, Path(work), cancel_event, progress_callback, use_cache
                ):
                    output.write(chunk)
            staged.replace(target)
        return True
    except OSError as exc:
        if not (cancel_event and cancel_event.is_set()):
            logging.warning(
                "MIRROR_FALLBACK: %s mirror failed (%s); trying upstream %s.",
                entry["id"],
                exc,
                entry["upstream_url"],
            )
        return False
