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
from dataclasses import dataclass
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


_process_scratch: list[str] = []


def input_scratch_directory() -> str:
    """A folder for a working copy made from an input when the caller has no
    output directory to fall back on (a plain VCF bgzipped so it can be
    indexed), so nothing is written beside the user's input. The run's
    Temporary Workload directory, else one folder in the system's temporary
    directory, removed when the process exits."""
    configured = configured_temp_directory()
    if configured:
        return configured
    if not _process_scratch:
        directory = tempfile.mkdtemp(prefix="wgse_inputs_")
        atexit.register(shutil.rmtree, directory, ignore_errors=True)
        _process_scratch.append(directory)
    return _process_scratch[0]


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
        shutil.copyfile(source, staged)
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
        shutil.copyfile(source, staged)
        os.link(staged, final)
    finally:
        staged.unlink(missing_ok=True)


@contextlib.contextmanager
def run_temporary_directory(override: PathInput | None = None) -> Iterator[None]:
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
