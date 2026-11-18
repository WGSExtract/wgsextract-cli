"""Running one operation: the result it records into, progress and cancellation."""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from wgsextract_cli.core import execution_context
from wgsextract_cli.core.execution_context import ConfigState
from wgsextract_cli.core.remote_input import check_public, is_url
from wgsextract_cli.core.results import (
    CommandResult,
    fail_from_exception,
    redact_params,
)

from ._params import Params, param_fields, with_config_defaults

P = TypeVar("P", bound=Params)

# Exit code of a cancelled operation, as for a CLI run stopped with Ctrl-C.
EXIT_CANCELLED = 130


@dataclass(frozen=True)
class ProgressEvent:
    """One progress report: a stage name, a message and, when known, how far
    through the operation is (0.0 to 1.0).

    Stages so far: ``start`` and ``finish`` (the command name, 0.0 and
    1.0), ``tool`` (an external tool is starting), ``step`` (a resumable
    workflow runs or skips one of its steps, and why), ``heartbeat`` (a
    periodic count from a long tool run, such as ``"1,200/5,000 reads"``, with
    the fraction of that run done when the total is known) and ``contig`` (a
    per-contig worker finished, such as ``"chr2 (3/25 contigs)"``, with the
    fraction of the contigs' bases done so far, or of the contigs when their
    lengths are not known, as in ``microarray``). More stages may be added."""

    stage: str
    message: str = ""
    fraction: float | None = None


class ProgressCallback(Protocol):
    """Receives progress events, from the calling thread or a worker thread."""

    def __call__(self, event: ProgressEvent, /) -> None: ...


class CancelToken:
    """Cooperative cancellation of a running operation.

    ``cancel()`` may be called from any thread. It stops the operation's
    external tools at once and makes the operation fail as cancelled at its
    next checkpoint. A token belongs to one operation.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logging.debug("Cancel callback failed.", exc_info=True)

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def is_set(self) -> bool:
        """Alias of ``cancelled``, so a token works where a ``threading.Event`` does."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled("Cancelled.")

    def on_cancel(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call ``callback`` on cancellation (at once if already cancelled);
        returns a function that unregisters it."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return lambda: self._remove(callback)
        callback()
        return lambda: None

    def _remove(self, callback: Callable[[], None]) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._callbacks.remove(callback)


class Cancelled(Exception):
    """Raised inside an operation when its ``CancelToken`` was cancelled."""


class CommandFailed(Exception):
    """An operation failed; ``result`` holds the error, warnings and partial outputs."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        error = result.error
        super().__init__(error.message if error else f"exit code {result.exit_code}")

    @property
    def exit_code(self) -> int:
        return self.result.exit_code


# Quick reads that describe a sample, the reference library or the tools and
# change nothing, so library calls of them run concurrently unless a ``writes``
# field asks them to save files (see ``_runs_concurrently``); other operations
# run one at a time per process (see ``_LOCK``). The server offers these
# without --allow-jobs.
READ_ONLY_COMMANDS = frozenset(
    {
        "info",
        "info chip",
        "sample show",
        "ref status",
        "ref library-list",
        "example-genome list",
        "deps check",
    }
)

# Serializes the operations that write files or run long, and config changes.
_LOCK = threading.RLock()


def _runs_concurrently(params: Params) -> bool:
    """Whether a library call with ``params`` runs beside others rather than
    one at a time: a read-only command whose ``writes`` fields save nothing,
    except the ``ATOMIC_WRITES`` ones, whose saves may race."""
    return params.COMMAND in READ_ONLY_COMMANDS and not any(
        item.spec.writes
        and item.name not in params.ATOMIC_WRITES
        and getattr(params, item.name) not in (None, item.safe_value)
        for item in param_fields(type(params))
    )


def report(stage: str, message: str = "", fraction: float | None = None) -> None:
    """Send a progress event to the running operation's callback, if any."""
    callback = execution_context.current().progress
    if callback is None:
        return
    try:
        callback(ProgressEvent(stage, message, fraction))
    except Exception:
        logging.debug("Progress callback failed.", exc_info=True)


def checkpoint() -> None:
    """Raise ``Cancelled`` if the running operation was cancelled."""
    token = execution_context.current().cancel
    if token is not None:
        token.raise_if_cancelled()


def _before_tool(command: Sequence[str]) -> None:
    checkpoint()
    if command:
        report("tool", f"Running {Path(command[0]).name}.")
        checkpoint()  # the callback may have cancelled


def cancel_event() -> CancelToken | None:
    """The running operation's token, for ``run_pipeline(cancel_event=...)``."""
    return execution_context.current().cancel


def run_inline(
    params: P, impl: Callable[[P], None], explicit: Collection[str] = ()
) -> None:
    """Run ``impl`` inside the caller's active result (the CLI's dispatch).

    ``explicit`` names fields the user set, which config must not override even
    when their value is None (``--threads auto``)."""
    from wgsextract_cli.core.config import settings
    from wgsextract_cli.core.output_location import remove_if_empty

    filled = with_config_defaults(params, keep=explicit)
    outdir = getattr(filled, "outdir", None)
    # The command creates its default output folder; a run that leaves it
    # empty (it failed, or wrote nothing) doesn't leave an empty wgse/ behind.
    # A folder the user named stays, even empty.
    default = getattr(params, "outdir", None) is None and not settings.get(
        "output_directory"
    )
    fresh = (
        Path(outdir).expanduser()
        if default and outdir and not Path(outdir).expanduser().exists()
        else None
    )
    try:
        with execution_context.bound(allow_downloads=_allows_downloads(filled)):
            impl(filled)
    finally:
        remove_if_empty(fresh)


def _allows_downloads(params: Params) -> bool:
    return (
        params.COMMAND
        in {
            "ref aadr",
            "ref somalier",
            "ref imputation",
            "ref ancestry",
            "ref gnomad-sv",
            "ref ybrowse",
            "ref download",
            "ref library",
            "ref test-genome",
            "ref gene-map",
            "ref clinvar",
            "ref revel",
            "ref phylop",
            "ref gnomad",
            "ref spliceai",
            "ref alphamissense",
            "ref pharmgkb",
            "ref cadd",
            "ref gerp",
            "ref acmg-data",
            "ref bootstrap",
            "ref microarray-targets",
            "vep download",
            "example-genome download",
        }
        or (
            params.COMMAND == "benchmark"
            and getattr(params, "dataset", "fake") in {"real", "real-1x", "real-30x"}
        )
        or bool(getattr(params, "download", False))
    )


def use_config(path: str | os.PathLike[str] | None = None) -> None:
    """Read settings for later calls from ``path``, or from the user's
    config.toml when None. Raises ``WGSExtractError`` for an invalid file.

    This sets the process's configuration, even when called inside a
    ``scoped_config`` block or a progress callback; ``scoped_config`` gives one
    thread's calls their own."""
    from wgsextract_cli.core.config import select_process_config

    with _LOCK:
        select_process_config(path)


@contextlib.contextmanager
def scoped_config(path: str | os.PathLike[str] | None = None) -> Iterator[None]:
    """Run the calls made in the block, and the worker threads they start,
    with settings read from ``path`` (the user's config.toml when None), while
    other threads keep theirs. Raises ``WGSExtractError`` for an invalid file."""
    from wgsextract_cli.core.config import scoped_config as config_scope

    with config_scope(path):
        yield


def execute(
    params: P,
    impl: Callable[[P], None],
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    check: bool = True,
    write_manifest: bool = True,
) -> CommandResult:
    """Run one operation as a library call and return its result.

    The human-readable report is not printed but kept in ``Result.text``, and
    external tools' stdout goes to stderr; the process's stdout is not
    redirected, so the caller's own writes (from a progress callback or other
    threads) still reach it. With ``check`` a failed or cancelled operation
    raises ``CommandFailed`` carrying the result; without it the
    result is returned with ``status`` and ``error`` set. The manifest is
    written next to the outputs as the CLI does, unless ``write_manifest`` is
    false. ``KeyboardInterrupt`` propagates, after the operation's tools are
    stopped. config.toml is loaded on the first call unless the host already
    loaded or selected one (see ``use_config``).
    """
    read_only = _runs_concurrently(params)
    with contextlib.nullcontext() if read_only else _LOCK:
        result, error, created = _run(params, impl, progress, cancel, read_only)
        if write_manifest:
            try:
                result.write_manifest()
            except OSError as e:
                logging.warning(f"Could not write the result manifest: {e}")
        from wgsextract_cli.core.output_location import remove_if_empty

        remove_if_empty(created)
    if check and result.exit_code:
        raise CommandFailed(result) from error
    return result


def _run(
    params: P,
    impl: Callable[[P], None],
    progress: ProgressCallback | None,
    cancel: CancelToken | None,
    read_only: bool,
) -> tuple[CommandResult, BaseException | None, Path | None]:
    """Run ``impl`` in a context of its own; the result, what failed it, and
    the default output folder it created (to remove if the run left it empty)."""
    from wgsextract_cli.core.atomic_output import atomic_output_mode
    from wgsextract_cli.core.config import (
        ensure_settings_loaded,
        settings,
        snapshot_config,
    )
    from wgsextract_cli.core.process_registry import proc_registry
    from wgsextract_cli.core.ref_library.lookup import local_reference_lookup
    from wgsextract_cli.core.temp_directory import run_temporary_directory

    # A read-only call, running beside others, reads the configuration as it
    # was when it started, unaffected by a later use_config.
    config: ConfigState | None
    if read_only:
        config = snapshot_config()
    else:
        ensure_settings_loaded()
        config = execution_context.current().config
    scope = object()  # this call's tools, for cancel
    result = CommandResult(command=params.COMMAND)
    error: BaseException | None = None
    with (
        execution_context.bound(
            result=result,
            json_mode=True,
            progress=progress,
            cancel=cancel,
            child_stdout_to_stderr=True,
            tool_hook=_before_tool,
            config=config,
            process_scope=scope,
        ),
        local_reference_lookup(),
    ):
        default_outdir = getattr(params, "outdir", None) is None and not settings.get(
            "output_directory"
        )
        params = with_config_defaults(params)
        result.provenance.params = redact_params(params.to_dict())
        unregister = (
            cancel.on_cancel(lambda: proc_registry.cancel(scope)) if cancel else None
        )
        created = None
        try:
            checkpoint()
            report("start", params.COMMAND, 0.0)
            created = _prepare(params, result)
            with (
                atomic_output_mode(bool(settings.get("atomic_outputs", False))),
                run_temporary_directory(None, fallback=getattr(params, "outdir", None)),
                execution_context.bound(allow_downloads=_allows_downloads(params)),
            ):
                impl(params)
            checkpoint()
            report("finish", params.COMMAND, 1.0)
        except KeyboardInterrupt:
            proc_registry.cleanup(scope)
            raise
        except (Exception, SystemExit) as e:
            error = e
            if cancel is not None and cancel.cancelled:
                logging.error("Cancelled.")
                result.fail("cancelled", "Cancelled.", EXIT_CANCELLED)
            else:
                fail_from_exception(result, e)
        finally:
            if unregister is not None:
                unregister()
            proc_registry.accept_new(scope)
    result.finalize(probe_tools=True)
    return result, error, created if default_outdir else None


def _prepare(params: Params, result: CommandResult) -> Path | None:
    """What dispatch does before a command: record and check the input, make
    the output folder."""
    from wgsextract_cli.cli.dispatch import checks_alignment_input, reports_status

    if reports_status(params):
        return None
    params.preflight()
    input_path = getattr(params, "input", None)
    if isinstance(input_path, str) and is_url(input_path):
        check_public(input_path)  # Before a secret in it is recorded.
        result.add_input(input_path, "input")
    if isinstance(input_path, Path):
        input_path = input_path.expanduser()
        if input_path.exists():
            result.add_input(input_path, "input")
        if input_path.suffix.lower() in {".bam", ".cram"} and checks_alignment_input(
            params.COMMAND
        ):
            from wgsextract_cli.core.alignment.input import validate_alignment_input

            validate_alignment_input(input_path)
    outdir = getattr(params, "outdir", None)
    if isinstance(outdir, Path):
        from wgsextract_cli.core.output_location import make_output_folder

        return make_output_folder(outdir)
    return None
