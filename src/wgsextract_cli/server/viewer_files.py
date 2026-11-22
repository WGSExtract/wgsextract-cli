"""Read-only guarded byte ranges, streamed without whole-file buffering."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from .operations import PathGuard, RequestError

MAX_FALLBACK_BYTES = 32 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


def safe_viewer_reads() -> bool:
    return (
        os.open in os.supports_dir_fd
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
    )


@contextmanager
def open_viewer_file(value: str, guard: PathGuard) -> Iterator[BinaryIO]:
    if not safe_viewer_reads():
        raise RequestError(
            503,
            "viewer_unavailable",
            "This platform lacks safe directory-relative file opens; viewer files are unavailable.",
        )
    if "\0" in value or ".." in re.split(r"[\\/]", value):
        raise RequestError(403, "path_not_allowed", "Viewer traversal is refused.")
    guard.check_path("path", value)
    path = Path(value).expanduser()
    try:
        canonical = path.resolve(strict=True)
    except (OSError, ValueError):
        raise RequestError(404, "not_found", "Viewer file does not exist.") from None
    root = next((r for r in guard.roots if canonical.is_relative_to(r)), None)
    if root is None:
        raise RequestError(
            403, "path_not_allowed", "Viewer path is outside allowed roots."
        )
    descriptor = -1
    file_descriptor = -1
    file: BinaryIO | None = None
    try:
        try:
            # Pin each directory: a concurrent rename or symlink retarget cannot
            # redirect the final open outside the configured root.
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            parts = canonical.relative_to(root).parts
            for part in parts[:-1]:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                previous = descriptor
                descriptor = child
                os.close(previous)
            file_descriptor = os.open(
                parts[-1] if parts else ".",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
            if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
                raise RequestError(
                    403, "not_regular", "Viewer paths must be regular files."
                )
            file = os.fdopen(file_descriptor, "rb")
            file_descriptor = -1
            guard.check_path("path", path)
            if not os.path.samestat(os.fstat(file.fileno()), canonical.stat()):
                raise RequestError(
                    409, "file_changed", "Viewer file changed; open it again."
                )
        except (OSError, ValueError):
            raise RequestError(
                403, "path_not_allowed", "Viewer file cannot be opened safely."
            ) from None
        yield file
    finally:
        try:
            if file is not None:
                file.close()
        finally:
            try:
                if file_descriptor >= 0:
                    os.close(file_descriptor)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)


def byte_range(
    header: str | None, size: int, *, bodyless: bool = False
) -> tuple[int, int, bool] | None:
    """The whole-file fallback limit bounds bodies; a HEAD can report any size."""
    if not header:
        return (0, size - 1, False) if bodyless or size <= MAX_FALLBACK_BYTES else None
    match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", header)
    if not match or not any(match.groups()) or size == 0:
        return None
    first, last = (int(v) if v else None for v in match.groups())
    start = first if first is not None else max(0, size - (last or 0))
    end = (
        size - 1
        if first is None
        else min(size - 1, last if last is not None else size - 1)
    )
    explicit = first is not None and last is not None
    if (
        start > end
        or start >= size
        or (not explicit and end - start + 1 > MAX_FALLBACK_BYTES)
    ):
        return None
    return start, end, True
