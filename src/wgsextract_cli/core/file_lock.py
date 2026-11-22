"""Cooperating builders serialize publication with an exclusive lock file."""

import contextlib
import errno
import json
import logging
import os
import socket
import sys
import time
import uuid
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


# The desktop app's Node backend shares these locks but has no flock, so they
# are files made with O_EXCL; gui/app/web/src/server/settings-lock.ts mirrors
# this protocol. A holder keeps one for a single read-modify-write.


def _shared_lock_record() -> bytes:
    return json.dumps(
        {"pid": os.getpid(), "host": socket.gethostname(), "token": uuid.uuid4().hex}
    ).encode()


def _owner_exited(record: bytes) -> bool:
    """Whether the process that wrote ``record`` has provably exited. An owner
    on another computer, or a record still being written, keeps its lock until
    it is released, so a slow save is never overtaken."""
    try:
        owner = json.loads(record)
        pid, host = int(owner["pid"]), str(owner["host"])
    except (ValueError, KeyError, TypeError):
        return False
    if host.casefold() != socket.gethostname().casefold():
        return False
    import psutil

    return not psutil.pid_exists(pid)


def _windows_busy(error: OSError) -> bool:
    # Windows refuses to create or delete a file another process is deleting
    # or reading.
    return sys.platform == "win32" and isinstance(error, PermissionError)


def _create_shared_lock(lock: Path, record: bytes) -> bool:
    """Create ``lock`` holding ``record``; False if it already exists."""
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    except FileExistsError:
        return False
    try:
        os.write(fd, record)
    except BaseException:
        os.close(fd)
        lock.unlink(missing_ok=True)
        raise
    os.close(fd)
    return True


def _remove_shared_lock(lock: Path, record: bytes) -> bool:
    """Delete ``lock`` if it still holds ``record``; False if it is busy."""
    try:
        if lock.read_bytes() == record:
            lock.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        if not _windows_busy(error):
            raise
        return False
    return True


def _release_shared_lock(lock: Path, record: bytes) -> None:
    for _ in range(100):
        if _remove_shared_lock(lock, record):
            return
        time.sleep(0.02)
    logging.warning(f"Could not remove lock {lock}")


def _remove_if_owner_exited(lock: Path) -> None:
    try:
        record = lock.read_bytes()
    except FileNotFoundError:
        return
    except OSError as error:
        if _windows_busy(error):
            return
        raise
    if _owner_exited(record) and _remove_shared_lock(lock, record):
        logging.info(f"Removed abandoned lock {lock}")


def _reclaim_abandoned_shared_lock(lock: Path) -> None:
    # One reclaimer at a time, so none deletes the lock a saver has just made
    # in place of the abandoned one another reclaimer removed.
    guard = lock.with_name(f"{lock.name}.reclaim")
    record = _shared_lock_record()
    try:
        if not _create_shared_lock(guard, record):
            _remove_if_owner_exited(guard)
            return
    except OSError as error:
        if _windows_busy(error):
            return
        raise
    try:
        _remove_if_owner_exited(lock)
    finally:
        _release_shared_lock(guard, record)


@contextlib.contextmanager
def shared_file_lock(path: Path, timeout: float = 60) -> Iterator[Path]:
    """Hold ``.<name>.lock`` beside the real file behind ``path`` while one
    read-modify-write of it runs, and yield that real path. Every alias of the
    file takes the same lock, and the desktop app's own saves take it too."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_name(f".{target.name}.lock")
    record = _shared_lock_record()
    deadline = time.monotonic() + timeout
    busy_since: float | None = None
    while True:
        try:
            if _create_shared_lock(lock, record):
                break
            busy_since = None
            _reclaim_abandoned_shared_lock(lock)
        except OSError as error:
            # A lock being deleted is busy for moments; a denial that lasts is
            # the folder refusing writes.
            busy_since = busy_since or time.monotonic()
            if not _windows_busy(error) or time.monotonic() - busy_since > 2:
                raise
        if time.monotonic() >= deadline:
            raise WGSExtractError(
                f"Timed out waiting for {lock}: another WGS Extract command or "
                "the app is saving this file. If neither is, delete the lock "
                "and try again."
            )
        time.sleep(0.02)
    try:
        yield target
    finally:
        _release_shared_lock(lock, record)
