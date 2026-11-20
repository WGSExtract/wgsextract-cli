"""Optional owned run workspace on a user-selected temporary volume."""

import atexit
import contextlib
import errno
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from wgsextract_cli.core.paths import PathInput, absolute_path, expand_path
from wgsextract_cli.core.utils import WGSExtractError


@dataclass(eq=False)
class _Workspace:
    # Mutable, so a worker thread (running in a copy of the context) can
    # retain the workspace of the run that started it.
    directory: str
    retained: bool = False


_workspace: ContextVar[_Workspace | None] = ContextVar(
    "temporary_workspace", default=None
)


def retain_temporary_directory() -> str | None:
    workspace = _workspace.get()
    if workspace is None:
        return None
    workspace.retained = True
    return workspace.directory


def configured_temp_directory() -> str | None:
    active = _workspace.get()
    if active is not None:
        return active.directory
    from wgsextract_cli.core.config import settings

    configured = settings.get("temp_directory")
    return str(absolute_path(expand_path(str(configured)))) if configured else None


def scratch_parent(fallback: PathInput) -> str:
    """The folder a command's temporary work goes in: the run's Temporary
    Workload directory (``--tmp-dir`` or ``temp_directory``), else
    ``fallback``. Pass the output directory as the fallback, never a folder
    holding the user's inputs: its disk already has room for the outputs, and
    the work, made in a hidden subfolder, is removed when the command ends.
    """
    return configured_temp_directory() or str(fallback)


@contextlib.contextmanager
def scratch_directory(fallback: PathInput, prefix: str) -> Iterator[str]:
    """A new folder for a command's temporary work under ``scratch_parent``,
    removed with everything in it when the block ends."""
    with tempfile.TemporaryDirectory(
        dir=scratch_parent(fallback), prefix=prefix
    ) as work:
        yield work


@dataclass
class _InputWorkspace:
    fallback: PathInput | None
    directories: dict[str | None, str] = field(default_factory=dict)
    cleanup: contextlib.ExitStack = field(default_factory=contextlib.ExitStack)


_inputs: ContextVar[_InputWorkspace | None] = ContextVar(
    "input_workspace", default=None
)
_process_scratch: dict[str | None, str] = {}


def input_scratch_directory(fallback: PathInput | None = None) -> str:
    """Keep prepared inputs in temporary work, else a cleaned hidden output
    subfolder. Only callers without an output/work folder use system temp;
    standalone preparation outside a run is cleaned at process exit."""
    configured = configured_temp_directory()
    if configured:
        return configured
    workspace = _inputs.get()
    parent = fallback or (workspace.fallback if workspace else None)
    parent = absolute_path(expand_path(parent)) if parent else None
    key = str(parent) if parent else None
    directories = workspace.directories if workspace else _process_scratch
    if key not in directories:
        if workspace:
            directory = workspace.cleanup.enter_context(
                tempfile.TemporaryDirectory(dir=parent, prefix=".wgse_inputs_")
            )
        else:
            directory = tempfile.mkdtemp(dir=parent, prefix=".wgse_inputs_")
            atexit.register(shutil.rmtree, directory, ignore_errors=True)
        directories[key] = directory
    return directories[key]


def move_file(source: PathInput, destination: PathInput) -> None:
    """Move a finished file out of the temporary work into place, replacing
    ``destination``. A rename when both are on one volume; otherwise (a
    ``--tmp-dir`` on another disk) a copy beside ``destination``, then a
    rename, so ``destination`` is never left half-written."""
    try:
        os.replace(source, destination)
        return
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
    final = Path(destination)
    staged = final.with_name(f".{final.name}.{uuid.uuid4().hex}.partial")
    try:
        shutil.copy2(source, staged)
        os.replace(staged, final)
    finally:
        staged.unlink(missing_ok=True)
    Path(source).unlink()


def link_new_file(source: PathInput, destination: PathInput) -> None:
    """Publish a finished file from the temporary work as ``destination``
    only if no file has that name: FileExistsError otherwise. A hard link on
    one volume; across volumes a copy beside ``destination``, then a link."""
    try:
        os.link(source, destination)
        return
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
    final = Path(destination)
    staged = final.with_name(f".{final.name}.{uuid.uuid4().hex}.partial")
    try:
        shutil.copy2(source, staged)
        os.link(staged, final)
    finally:
        staged.unlink(missing_ok=True)


@contextlib.contextmanager
def run_temporary_directory(
    override: PathInput | None = None, *, fallback: PathInput | None = None
) -> Iterator[None]:
    inputs = _InputWorkspace(fallback)
    token = _inputs.set(inputs)
    try:
        with inputs.cleanup, _run_workspace(override):
            yield
    finally:
        _inputs.reset(token)


@contextlib.contextmanager
def _run_workspace(override: PathInput | None) -> Iterator[None]:
    parent = (
        str(absolute_path(expand_path(override)))
        if override
        else configured_temp_directory()
    )
    if parent is None:
        yield
        return
    if not Path(parent).is_dir() or not os.access(parent, os.W_OK):
        raise WGSExtractError(
            f"Temporary directory must exist and be writable: {parent}. Create it first, or choose another --tmp-dir."
        )
    try:
        directory = tempfile.mkdtemp(dir=parent, prefix=".wgse_run_")
    except OSError as error:
        raise WGSExtractError(
            f"Cannot use temporary directory {parent}: {error}"
        ) from error
    workspace = _Workspace(directory)
    token = _workspace.set(workspace)
    try:
        yield
    finally:
        _workspace.reset(token)
        if not workspace.retained:
            shutil.rmtree(directory)
