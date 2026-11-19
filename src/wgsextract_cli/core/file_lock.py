"""Cooperating builders serialize publication with an exclusive lock file."""

import contextlib
import errno
import json
import logging
import os
import socket
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from wgsextract_cli.core.utils import WGSExtractError

# Chosen at import, so tests that patch sys.platform still lock natively.
if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def _reclaim_guard(
    path: Path,
    deadline: float,
    checkpoint: Callable[[], None] | None = None,
) -> Iterator[None]:
    # Keep this inode: deleting it would let concurrent reclaimers lock different files.
    guard = Path(f"{path}.guard")
    try:
        try:
            # The kernel applies the current umask; never replace the inode.
            fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o666)
        except FileExistsError:
            # O_CREAT can deny another user's file in a sticky shared directory.
            fd = os.open(guard, os.O_RDWR)
    except PermissionError as error:
        raise WGSExtractError(
            f"Cannot open lock guard {guard}: {error.strerror or error} "
            f"(errno {error.errno}). Ask the file "
            "owner or administrator to grant read/write access to this file "
            "and access to its parent directory for users sharing the library. "
            "Do not delete the guard while any run may be using the library."
        ) from error
    try:
        while True:
            if checkpoint is not None:
                checkpoint()
            try:
                _try_lock(fd)
                break
            except OSError as error:
                if error.errno not in {errno.EAGAIN, errno.EACCES}:
                    raise
                if time.monotonic() >= deadline:
                    raise WGSExtractError(
                        f"Timed out waiting for lock: {path}.guard"
                    ) from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            if checkpoint is not None:
                checkpoint()
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def exclusive_lock(
    path: Path,
    timeout: float = 30,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Hold an advisory lock on ``<path>.guard`` (kept between runs) for a short
    read-modify-write; waits up to ``timeout`` seconds for another holder.
    ``checkpoint``, when supplied, may raise to cancel acquisition or waiting."""
    with _reclaim_guard(path, time.monotonic() + timeout, checkpoint):
        yield


def _abandoned(path: Path, timeout: float) -> bool:
    import psutil

    try:
        record = json.loads(path.read_text())
        if record["host"] != socket.gethostname():
            return False
        try:
            process = psutil.Process(record["pid"])
            return bool(process.create_time() != record["created"])
        except psutil.NoSuchProcess:
            return True
        except psutil.AccessDenied:
            return False
    except (ValueError, KeyError, TypeError):
        return time.time() - path.stat().st_mtime > timeout


def held(lock: str, timeout: float = 1800) -> bool:
    """Whether a live run holds ``build_lock(lock)``. Call it inside
    ``exclusive_lock(Path(lock))`` so no builder takes the lock meanwhile."""
    path = Path(lock)
    return path.exists() and not _abandoned(path, timeout)


@contextlib.contextmanager
def build_lock(lock: str, timeout: float = 1800) -> Iterator[None]:
    """Reclaim dead local owners, never evict a live hour-long index builder."""
    import psutil

    path = Path(lock)
    deadline = time.monotonic() + timeout
    record = json.dumps(
        {
            "pid": os.getpid(),
            "created": psutil.Process().create_time(),
            "host": socket.gethostname(),
        }
    )
    while True:
        acquired = False
        with _reclaim_guard(path, deadline):
            if path.exists():
                if _abandoned(path, timeout):
                    path.unlink(missing_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
            except FileExistsError:
                pass
            else:
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                        stream.write(record)
                except BaseException:
                    path.unlink(missing_ok=True)
                    raise
                acquired = True
        if acquired:
            break
        if time.monotonic() >= deadline:
            raise WGSExtractError(
                f"Timed out waiting for index builder lock: {path}"
            ) from None
        logging.info(f"Waiting for another run to finish building {lock[:-5]}...")
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    try:
        yield
    finally:
        with _reclaim_guard(path, time.monotonic() + timeout):
            if path.exists() and path.read_text() == record:
                path.unlink(missing_ok=True)
