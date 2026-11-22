"""Handle-relative viewer opens on native Windows."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.USHORT),
            ("maximum_length", wintypes.USHORT),
            ("buffer", wintypes.LPWSTR),
        ]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.ULONG),
            ("root_directory", wintypes.HANDLE),
            ("object_name", ctypes.POINTER(_UnicodeString)),
            ("attributes", wintypes.ULONG),
            ("security_descriptor", ctypes.c_void_p),
            ("security_qos", ctypes.c_void_p),
        ]

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("status_or_pointer", ctypes.c_void_p),
            ("information", ctypes.c_size_t),
        ]

    _ntdll = ctypes.WinDLL("ntdll")
    _open_file = _ntdll.NtOpenFile
    _open_file.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        wintypes.ULONG,
        wintypes.ULONG,
    )
    _open_file.restype = wintypes.LONG
    _dos_error = _ntdll.RtlNtStatusToDosError
    _dos_error.argtypes = (wintypes.LONG,)
    _dos_error.restype = wintypes.ULONG

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = (wintypes.HANDLE,)
    _close_handle.restype = wintypes.BOOL
    _drive_type = _kernel32.GetDriveTypeW
    _drive_type.argtypes = (wintypes.LPCWSTR,)
    _drive_type.restype = wintypes.UINT

    _OBJ_CASE_INSENSITIVE = 0x40
    _OBJ_DONT_REPARSE = 0x1000
    _SYNCHRONIZE = 0x100000
    _FILE_TRAVERSE = 0x20
    _FILE_READ_DATA = 0x01
    _FILE_READ_ATTRIBUTES = 0x80
    _FILE_SHARE_READ = 0x01
    _FILE_SHARE_WRITE = 0x02
    _FILE_DIRECTORY_FILE = 0x01
    _FILE_NON_DIRECTORY_FILE = 0x40
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x20
    _LOCAL_DRIVE_TYPES = {2, 3, 5, 6}  # Removable, fixed, optical, RAM disk.

    def _open(
        name: str, parent: int | None, *, directory: bool, ignore_case: bool = False
    ) -> int:
        buffer = ctypes.create_unicode_buffer(name)
        length = len(buffer) * ctypes.sizeof(ctypes.c_wchar)
        if length > 65535:
            raise ValueError("Viewer path component is too long.")
        name_string = _UnicodeString(
            length - ctypes.sizeof(ctypes.c_wchar),
            length,
            ctypes.cast(buffer, wintypes.LPWSTR),
        )
        attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes),
            parent,
            ctypes.pointer(name_string),
            _OBJ_DONT_REPARSE | (_OBJ_CASE_INSENSITIVE if ignore_case else 0),
        )
        handle = wintypes.HANDLE()
        access = _SYNCHRONIZE | (
            _FILE_TRAVERSE if directory else _FILE_READ_DATA | _FILE_READ_ATTRIBUTES
        )
        options = _FILE_SYNCHRONOUS_IO_NONALERT | (
            _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
        )
        status = _open_file(
            ctypes.byref(handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(_IoStatusBlock()),
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            options,
        )
        if status < 0:
            raise ctypes.WinError(_dos_error(status))
        value: int | None = handle.value
        if value is None:
            raise OSError("Native viewer open returned no handle.")
        return value

    @contextmanager
    def open_windows_viewer(root: Path, parts: tuple[str, ...]) -> Iterator[BinaryIO]:
        if _drive_type(root.anchor) not in _LOCAL_DRIVE_TYPES:
            raise ValueError("Viewer files must be on a local drive.")
        if not parts or any(":" in part for part in parts):
            raise ValueError(
                "Viewer paths must name a file, not a directory or stream."
            )
        with ExitStack() as stack:
            # Walk from the volume with no reparse traversal and no delete sharing.
            # Keep each ancestor pinned while opening and reading the file.
            parent = _open(
                "\\??\\" + root.anchor, None, directory=True, ignore_case=True
            )
            stack.callback(_close_handle, parent)
            for part in root.parts[1:]:
                parent = _open(part, parent, directory=True)
                stack.callback(_close_handle, parent)
            for part in parts[:-1]:
                parent = _open(part, parent, directory=True, ignore_case=True)
                stack.callback(_close_handle, parent)
            handle = _open(parts[-1], parent, directory=False, ignore_case=True)
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                _close_handle(handle)
                raise
            try:
                file = os.fdopen(descriptor, "rb")
            except BaseException:
                os.close(descriptor)
                raise
            with file:
                yield file
