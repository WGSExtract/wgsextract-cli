"""Output encoding and terminal capabilities, without setting up run logging."""

import sys
from functools import cache


def configure_stdio_encoding() -> None:
    """Use UTF-8 even for Windows pipes, whose ANSI code page loses log symbols."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            import logging

            logging.debug("Could not reconfigure stdio encoding.", exc_info=True)


@cache
def enable_windows_vt(fd: int) -> bool:
    """Turn on ANSI support for a console, returning false if it refuses."""
    if sys.platform != "win32":
        return False
    import ctypes
    import msvcrt
    from ctypes import wintypes

    enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        handle = msvcrt.get_osfhandle(fd)
    except OSError:
        return False
    mode = wintypes.DWORD()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    if mode.value & enable_vt:
        return True
    return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
