import logging
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType
from typing import TextIO

SIGNAL_EXPLANATIONS = {
    "SIGHUP": "terminal or parent session closed",
    "SIGINT": "interrupt from keyboard, usually Ctrl+C",
    "SIGQUIT": "quit from keyboard, usually Ctrl+\\",
    "SIGKILL": "force kill; cannot be caught or cleaned up",
    "SIGTERM": "normal termination request from kill or a process manager",
}

_stop_signal: int | None = None


def received_stop_signal() -> int | None:
    """The CLI's stop request, retained if cleanup replaces the handler's exit."""
    return _stop_signal


def describe_signal(signum: int) -> str:
    """Return a concise user-facing explanation for a signal number."""
    import signal

    try:
        signal_name = signal.Signals(signum).name
    except ValueError:
        return f"signal {signum}: unknown signal"

    explanation = SIGNAL_EXPLANATIONS.get(signal_name, "process signal")
    return f"signal {signum} ({signal_name}: {explanation})"


def _parent_process_is_alive(parent_pid: int) -> bool:
    import psutil

    return parent_pid > 0 and bool(psutil.pid_exists(parent_pid))


class EmojiFormatter(logging.Formatter):
    """Custom formatter to add emojis to log levels."""

    LEVEL_EMOJIS = {
        logging.DEBUG: "🔍",
        logging.INFO: "ℹ️",
        logging.WARNING: "⚠️",
        logging.ERROR: "❌",
        logging.CRITICAL: "🚨",
    }

    def format(self, record: logging.LogRecord) -> str:
        # Format a copy so other handlers still see the real level name.
        record = logging.makeLogRecord(record.__dict__)
        record.levelname = self.LEVEL_EMOJIS.get(record.levelno, record.levelname)
        return super().format(record)


def configure_stdio_encoding() -> None:
    """UTF-8 output everywhere. Piped or redirected output on Windows would
    otherwise use the ANSI code page (cp1252), which has none of the log
    symbols, so the app and log files saw "??" for each."""
    for stream in (sys.stdout, sys.stderr):
        _reconfigure_stream(stream)


def _reconfigure_stream(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        logging.debug("Could not reconfigure stdio encoding.", exc_info=True)


def _cli_log_handler() -> logging.StreamHandler[TextIO]:
    handler = logging.StreamHandler()
    handler.setFormatter(
        EmojiFormatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
    )
    return handler


def _log_level(debug: bool, quiet: bool) -> int:
    return logging.DEBUG if debug else logging.ERROR if quiet else logging.INFO


def configure_logging(*, debug: bool = False, quiet: bool = False) -> None:
    handler = _cli_log_handler()
    level = _log_level(debug, quiet)
    handler.setLevel(level)
    logging.basicConfig(level=level, handlers=[handler], force=True)


@contextmanager
def cli_logging(*, debug: bool, quiet: bool) -> Iterator[Callable[[bool, bool], None]]:
    """Format CLI diagnostics without replacing an embedding caller's handlers."""
    root = logging.getLogger()
    previous_level = root.level
    handler = _cli_log_handler()

    def set_level(debug: bool, quiet: bool) -> None:
        level = _log_level(debug, quiet)
        root.setLevel(level)
        handler.setLevel(level)

    set_level(debug, quiet)
    root.addHandler(handler)
    try:
        yield set_level
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(previous_level)


def install_signal_handlers() -> None:
    import signal

    from wgsextract_cli.core.execution_context import current
    from wgsextract_cli.core.process_registry import cleanup_processes

    global _stop_signal
    _stop_signal = None

    def signal_handler(signum: int, frame: FrameType | None) -> None:
        global _stop_signal
        _stop_signal = signum
        logging.info(f"Received {describe_signal(signum)}, cleaning up...")
        if token := current().cancel:
            token.cancel()
        cleanup_processes()
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def start_parent_monitor(parent_pid: int | None) -> None:
    if not parent_pid:
        return

    import threading
    import time

    from wgsextract_cli.core.process_registry import cleanup_processes

    def monitor_parent() -> None:
        while True:
            if not _parent_process_is_alive(parent_pid):
                logging.warning(f"Parent process {parent_pid} disappeared, exiting...")
                cleanup_processes()
                os._exit(1)
            time.sleep(2)

    monitor_thread = threading.Thread(target=monitor_parent, daemon=True)
    monitor_thread.start()
