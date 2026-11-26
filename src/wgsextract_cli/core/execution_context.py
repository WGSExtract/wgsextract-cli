"""The running operation's state, scoped to it rather than to the process.

An ``ExecutionContext`` holds what a command reads while it runs: the result
it records into, its progress callback and cancel token, where its tools'
stdout goes, the hook called before each tool starts, its configuration and
the key its tools are registered under for cancellation. It lives in a
``ContextVar``, so library calls on different threads each see their own;
``bound`` changes fields for the duration of a ``with`` block.

A new thread starts with an empty context. Worker threads that run part of a
command must start through ``context_thread`` or ``ContextThreadPoolExecutor``,
which run them in a copy of the starting thread's context, so they report to
the same result, honour the same cancellation and read the same settings.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import threading

    from wgsextract_cli.api._runtime import CancelToken, ProgressCallback
    from wgsextract_cli.core._context_executor import (
        ContextThreadPoolExecutor as ContextThreadPoolExecutor,
    )
    from wgsextract_cli.core.results import CommandResult


@dataclass(eq=False)
class ConfigState:
    """One configuration: the config.toml it is read from (None: the user's)
    and the settings read from it."""

    path: Path | None = None
    values: dict[str, Any] = field(default_factory=dict)
    loaded: bool = False


@dataclass(frozen=True)
class ExecutionContext:
    """What the running operation reads; ``None`` fields mean none is set.

    ``config`` None means the process's configuration (``use_config``);
    ``process_scope`` keys the tools this operation starts, so cancelling it
    stops only them."""

    result: CommandResult | None = None
    json_mode: bool = False
    progress: ProgressCallback | None = None
    cancel: CancelToken | None = None
    child_stdout_to_stderr: bool = False
    tool_hook: Callable[[Sequence[str]], None] | None = None
    config: ConfigState | None = None
    process_scope: object | None = None
    allow_downloads: bool | None = None
    result_card: bool | None = None  # None: config.toml's result_card


_EMPTY = ExecutionContext()  # frozen, so a safe shared default
_CURRENT: contextvars.ContextVar[ExecutionContext] = contextvars.ContextVar(
    "wgsextract_execution_context", default=_EMPTY
)


def current() -> ExecutionContext:
    """The running operation's context (an empty one outside any)."""
    return _CURRENT.get()


@contextlib.contextmanager
def bound(**changes: Any) -> Iterator[ExecutionContext]:
    """Run the block with these fields of the current context replaced."""
    context = replace(_CURRENT.get(), **changes)
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


def _reset_in_child() -> None:
    # A forked worker (a process pool) must not call the caller's progress
    # callback or cancel a copy of its token; the parent does both.
    context = _CURRENT.get()
    if context.progress is not None or context.cancel is not None:
        _CURRENT.set(replace(context, progress=None, cancel=None))


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_in_child)


def context_thread(
    target: Callable[..., object],
    *,
    args: Iterable[Any] = (),
    kwargs: Mapping[str, Any] | None = None,
    name: str | None = None,
    daemon: bool | None = None,
) -> threading.Thread:  # noqa: TID251
    """A ``threading.Thread`` (not started) running ``target`` in a copy of
    the calling thread's context."""
    import threading

    context = contextvars.copy_context()
    return threading.Thread(  # noqa: TID251
        target=context.run,
        args=(target, *args),
        kwargs=dict(kwargs or {}),
        name=name,
        daemon=daemon,
    )


def __getattr__(name: str) -> object:
    # Reading the current config does not need an executor or its worker setup.
    if name == "ContextThreadPoolExecutor":
        from wgsextract_cli.core._context_executor import ContextThreadPoolExecutor

        return ContextThreadPoolExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
