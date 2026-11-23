"""Coordinate CLI operations with the desktop app's workspace moves."""

import json
import os
import re
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from platformdirs import user_data_dir

from wgsextract_cli.core.errors import WGSExtractError


@dataclass
class _Lease:
    active: bool = True


@dataclass
class WorkspaceLease:
    name: str
    cleanup: ExitStack


_lease: ContextVar[_Lease | None] = ContextVar("workspace_activity", default=None)
_marker = re.compile(r"(use|move)-([1-9]\d*)-[a-f0-9-]+")


def _standard_desktop_data_dir() -> Path:
    """Use the same local application-support directory as the Node app."""
    if sys.platform == "win32":
        return (
            Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
            / "WGSExtract-gui"
        )
    return Path(user_data_dir("WGSExtract-gui", appauthor=False))


def configured_desktop_data_dir() -> Path | None:
    """App data supplied by the GUI for its own bundle subprocesses."""
    override = Path(os.environ.get("WGSEXTRACT_GUI_APP_DATA", ""))
    return override if override.is_absolute() else None


def desktop_data_dir() -> Path:
    """Read the location record from the calling app, or the standard app."""
    return configured_desktop_data_dir() or _standard_desktop_data_dir()


def _config_key(path: Path, *, resolve: bool = False) -> str:
    # A recorded UNC path is not a reason to contact its server while looking
    # up an unrelated local config. Use its spelling for the redirect key.
    drive = path.drive.upper().replace("\\\\?\\UNC\\", "\\\\", 1)
    if resolve and not drive.removeprefix("\\\\?\\").startswith("\\\\"):
        path = path.resolve()
    return os.path.normcase(path)


def _redirect_keys(redirects: dict[str, str]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for raw, target in redirects.items():
        source = Path(raw)
        if not source.is_absolute():
            continue
        keys[_config_key(source)] = target
        try:
            keys.setdefault(_config_key(source, resolve=True), target)
        except (OSError, RuntimeError, ValueError):
            continue  # An obsolete alias cannot invalidate unrelated redirects.
    return keys


def relocated_config_path(path: Path) -> Path:
    """Follow a moved settings file, but let a real file at the old path win."""
    if path.exists():
        return path
    settings = desktop_data_dir() / "settings.json"
    try:
        redirects = json.loads(settings.read_text(encoding="utf-8")).get(
            "movedConfigPaths"
        )
    except (OSError, ValueError, AttributeError):
        return path
    if not isinstance(redirects, dict):
        return path
    by_path = _redirect_keys(redirects)
    seen: set[str] = set()
    while not path.exists() and isinstance(
        target := by_path.get(key := _config_key(path, resolve=True)), str
    ):
        if not Path(target).is_absolute() or key in seen:
            raise WGSExtractError(f"Invalid moved settings path: {path}")
        seen.add(key)
        path = Path(target).resolve()
    return path


@contextmanager
def workspace_use(admitted_by: str | None = None) -> Iterator[None]:
    """Announce work before resolving its paths, and hold through publication."""
    outer = _lease.get()
    if outer is not None and outer.active:
        yield
        return

    with hold_workspace(admitted_by).cleanup:
        lease = _Lease()
        token = _lease.set(lease)
        try:
            yield
        finally:
            lease.active = False
            _lease.reset(token)


def _pid_exists(pid: int) -> bool:
    from psutil import pid_exists

    return bool(pid_exists(pid))


def hold_workspace(admitted_by: str | None = None) -> WorkspaceLease:
    """Acquire a lease that can be handed to the thread finishing a queued job."""
    # The Node app also uses this fixed location: a standalone CLI must see a
    # move even when it did not inherit the launching GUI's data override.
    directory = _standard_desktop_data_dir() / "workspace-activity"
    marker = directory / f"use-{os.getpid()}-{uuid4()}"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Publish before looking for moves. A mover checks for users only
    # after publishing its own marker. No file handle stays open.
    marker.touch(exist_ok=False)
    with ExitStack() as cleanup:
        cleanup.callback(marker.unlink, missing_ok=True)
        if admitted_by is not None:
            # The manager already prevented any move from starting. Publish
            # the worker's own marker first, so it stays protected if the
            # manager exits while the worker is cancelling.
            admission = _marker.fullmatch(admitted_by)
            if (
                not admission
                or admission[1] != "use"
                or int(admission[2]) != os.getppid()
                or not (directory / admitted_by).is_file()
                or not _pid_exists(int(admission[2]))
            ):
                raise WGSExtractError("The job lost its workspace lease.")
            return WorkspaceLease(marker.name, cleanup.pop_all())
        for other in directory.iterdir():
            if other == marker:
                continue
            match = _marker.fullmatch(other.name)
            if match is None:
                continue
            if not _pid_exists(int(match[2])):
                other.unlink(missing_ok=True)
            elif match[1] == "move":
                raise WGSExtractError(
                    "The workspace is being moved. Try again when the move has finished."
                )
        return WorkspaceLease(marker.name, cleanup.pop_all())
