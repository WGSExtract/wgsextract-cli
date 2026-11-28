"""Own a Windows subprocess tree independently of its launcher."""

import ctypes
import logging
import sys
import threading
import weakref
from ctypes import wintypes

_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _create = _kernel32.CreateJobObjectW
    _create.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _create.restype = wintypes.HANDLE
    _set_info = _kernel32.SetInformationJobObject
    _set_info.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _set_info.restype = wintypes.BOOL
    _assign = _kernel32.AssignProcessToJobObject
    _assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _assign.restype = wintypes.BOOL
    _query = _kernel32.QueryInformationJobObject
    _query.argtypes = [*_set_info.argtypes, ctypes.POINTER(wintypes.DWORD)]
    _query.restype = wintypes.BOOL
    _terminate = _kernel32.TerminateJobObject
    _terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _terminate.restype = wintypes.BOOL
    _close = _kernel32.CloseHandle
    _close.argtypes = [wintypes.HANDLE]
    _close.restype = wintypes.BOOL


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]
    LimitFlags: int


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", ctypes.c_ulonglong * 6),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]
    BasicLimitInformation: _BasicLimits


class _BasicAccounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]
    ActiveProcesses: int


class WindowsJob:
    def __init__(self, process_handle: int) -> None:
        if sys.platform == "win32":
            self._lock = threading.RLock()
            handle = _create(None, None)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = handle
            self._close = weakref.finalize(self, _close, handle)
            self._close.atexit = False  # The registry terminates live jobs at exit.
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            try:
                if not _set_info(
                    handle,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(limits),
                    ctypes.sizeof(limits),
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not _assign(handle, process_handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            except BaseException:
                self.close()
                raise
        else:
            raise OSError("Windows Job Objects are unavailable on this platform")

    def has_processes(self) -> bool:
        if sys.platform == "win32":
            with self._lock:
                if self._close.alive:
                    info = _BasicAccounting()
                    if _query(
                        self._handle,
                        _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                        ctypes.byref(info),
                        ctypes.sizeof(info),
                        None,
                    ):
                        return info.ActiveProcesses != 0
                    if self._close.alive:
                        logging.warning(
                            "Could not query the Windows job: %s",
                            ctypes.WinError(ctypes.get_last_error()),
                        )
        return False

    def close(self) -> None:
        """Close the only handle, terminating all remaining members."""
        if sys.platform == "win32":
            with self._lock:
                self._close()

    def terminate(self) -> None:
        if sys.platform == "win32":
            with self._lock:
                try:
                    # Kill-on-close alone can give cancelled tools exit code 0.
                    if self._close.alive and not _terminate(self._handle, 1):
                        if self._close.alive:
                            logging.warning(
                                "Could not terminate the Windows job: %s",
                                ctypes.WinError(ctypes.get_last_error()),
                            )
                finally:
                    self._close()
