"""Best-effort removal of unwritten LLVM OpenMP registrations before tests."""

import os
import re
import stat
from pathlib import Path

_REGISTRATION = re.compile(r"__KMP_REGISTERED_LIB_([1-9][0-9]{0,9})")
_UNWRITTEN = bytes(1024)  # LLVM's file-backed registration size.


def _pid_is_absent(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, OverflowError):
        # EPERM may mean a live process owned by someone else.
        pass
    return False


def _snapshot(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def sweep_unwritten_openmp_registrations(directory: Path) -> None:
    """Remove our own empty/zero-filled PID markers, not other registrations."""
    uid = os.getuid()
    try:
        candidates = list(directory.glob("__KMP_REGISTERED_LIB_*"))
    except OSError:
        return
    for path in candidates:
        match = _REGISTRATION.fullmatch(path.name)
        if not match:
            continue
        try:
            before = path.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != uid
                or before.st_nlink != 1
                or before.st_size not in (0, len(_UNWRITTEN))
            ):
                continue
            if before.st_size:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    if _snapshot(os.fstat(stream.fileno())) != _snapshot(before):
                        continue
                    if stream.read(len(_UNWRITTEN) + 1) != _UNWRITTEN:
                        continue
            # Recheck both the file and PID immediately before removal.
            if _snapshot(path.lstat()) == _snapshot(before) and _pid_is_absent(
                int(match[1])
            ):
                path.unlink()
        except OSError:
            continue
