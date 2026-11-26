"""Thread pools that carry the submitting operation's context into workers."""

import contextvars
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor  # noqa: TID251
from typing import Any, TypeVar

T = TypeVar("T")


class _ContextFuture(Future[T]):
    """Run done-callbacks in the context in which they were added."""

    def add_done_callback(self, fn: Callable[[Future[T]], object]) -> None:
        context = contextvars.copy_context()
        super().add_done_callback(lambda future: context.run(fn, future))


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    """Run tasks with the submitting operation's result, cancellation and settings."""

    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Future[T]:
        future = super().submit(contextvars.copy_context().run, fn, *args, **kwargs)
        # The executor makes a plain Future; give it context-bound callbacks
        # before anyone can add one.
        future.__class__ = _ContextFuture
        return future
