"""Reusable real-tool inputs; tests always receive their own writable copies."""

import ctypes
import hashlib
import inspect
import shutil
import sys
from collections.abc import Callable
from functools import cache
from pathlib import Path

from wgsextract_cli.core.file_lock import build_lock

_WINDOWS = sys.platform == "win32"


@cache
def _recipe_identity() -> bytes:
    root = Path(__file__).resolve().parents[1]
    return hashlib.sha256(
        Path(__file__).read_bytes() + (root / "pixi.lock").read_bytes()
    ).digest()


@cache
def _tool_identity(tool: str) -> bytes:
    return hashlib.sha256(Path(tool).read_bytes()).digest()


def prepared_files(
    content: bytes,
    tool: str,
    names: tuple[str, ...],
    build: Callable[[Path], None],
) -> Path:
    """Cache by input, helper implementation and tool binary, across workers."""
    digest = hashlib.sha256(
        content
        + _recipe_identity()
        + inspect.getsource(build).encode()
        + _tool_identity(tool)
        + repr(names).encode()
    ).hexdigest()
    root = Path(__file__).resolve().parents[1] / "out" / "test-fixtures" / digest
    root.mkdir(parents=True, exist_ok=True)
    with build_lock(str(root) + ".lock"):
        if not (root / "complete").is_file() or not all(
            (root / name).is_file() for name in names
        ):
            (root / "complete").unlink(missing_ok=True)
            build(root)
            (root / "complete").touch()
    return root


def copy_prepared(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def sparse_truncate(path: Path, size: int) -> None:
    """Resize without allocating tens of GiB on NTFS (POSIX already does this)."""
    with path.open("r+b" if path.exists() else "w+b") as stream:
        if _WINDOWS:
            import msvcrt
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            ioctl = kernel.DeviceIoControl
            ioctl.argtypes = (
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                wintypes.LPVOID,
            )
            ioctl.restype = wintypes.BOOL
            returned = wintypes.DWORD()
            # FSCTL_SET_SPARSE preserves existing bytes when growing an index.
            if not ioctl(
                msvcrt.get_osfhandle(stream.fileno()),
                0x900C4,
                None,
                0,
                None,
                0,
                ctypes.byref(returned),
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        stream.truncate(size)
