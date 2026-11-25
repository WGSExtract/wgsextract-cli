"""Which recorded paths a run may follow.

A command that follows paths other runs recorded (``report`` reads the outputs
their manifests list) asks :func:`may_follow` first. A server job limits that
to the folders the server allows, so a manifest can't lead it elsewhere; on
the command line every path may be followed. The wgsextract subprocesses a
run starts (``analyze``'s steps, ``vcf chain-annotate``'s annotators) are
limited to the same folders: :func:`child_args` passes them on, and the
child's dispatcher applies them (:func:`from_argument`).
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class _Scope:
    allows: Callable[[str], bool]
    folders: tuple[str, ...] | None  # None: not expressible as folders


_SCOPE: ContextVar[_Scope | None] = ContextVar("read_scope", default=None)


@contextmanager
def limited_to(
    allows: Callable[[str], bool],
    folders: Iterable[str | os.PathLike[str]] | None = None,
) -> Iterator[None]:
    """Follow only the recorded paths ``allows`` accepts, until the end.
    ``folders`` are the folders it accepts, for subprocesses; without them a
    subprocess may follow nothing."""
    listed = None if folders is None else tuple(os.fspath(f) for f in folders)
    scope = _Scope(allows, listed)
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def limited_to_folders(
    folders: Iterable[str | os.PathLike[str]],
) -> contextlib.AbstractContextManager[None]:
    """Follow only paths in ``folders``, as a server checks request paths."""
    from wgsextract_cli.server.operations import PathGuard

    folders = [os.fspath(f) for f in folders]
    return limited_to(PathGuard(folders).allows, folders)


def may_follow(path: str | os.PathLike[str]) -> bool:
    scope = _SCOPE.get()
    return scope is None or scope.allows(os.fspath(path))


def child_args() -> list[str]:
    """The hidden global option limiting a wgsextract subprocess to this
    run's folders; none outside a limited run."""
    scope = _SCOPE.get()
    if scope is None:
        return []
    return ["--read-scope", json.dumps(list(scope.folders or ()))]


def from_argument(text: str | None) -> contextlib.AbstractContextManager[None]:
    """The scope ``--read-scope`` (a JSON list of folders) names; a value
    that isn't one allows nothing rather than everything."""
    if text is None:
        return contextlib.nullcontext()
    try:
        folders = json.loads(text)
    except ValueError:
        folders = []
    if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
        folders = []
    return limited_to_folders(folders)
