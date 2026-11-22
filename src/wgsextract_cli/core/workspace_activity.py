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


_lease: ContextVar[_Lease | None] = ContextVar("workspace_activity", default=None)
_marker = re.compile(r"(use|move)-([1-9]\d*)-[a-f0-9-]+")


def desktop_data_dir() -> Path:
    """Use the same local application-support directory as the Node app."""
    if sys.platform == "win32":
        return (
            Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
            / "WGSExtract-gui"
        )
    return Path(user_data_dir("WGSExtract-gui", appauthor=False))


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
    by_path = {Path(source): target for source, target in redirects.items()}
    seen: set[Path] = set()
    while not path.exists() and isinstance(target := by_path.get(path), str):
        if not Path(target).is_absolute() or path in seen:
            raise WGSExtractError(f"Invalid moved settings path: {path}")
        seen.add(path)
        path = Path(target).resolve()
    return path


@contextmanager
def workspace_use() -> Iterator[None]:
    """Announce work before resolving its paths, and hold through publication."""
    outer = _lease.get()
    if outer is not None and outer.active:
        yield
        return

    with hold_workspace():
        lease = _Lease()
        token = _lease.set(lease)
        try:
            yield
        finally:
            lease.active = False
            _lease.reset(token)


def hold_workspace() -> ExitStack:
    """Acquire a lease that can be handed to the thread finishing a queued job."""
    import psutil

    directory = desktop_data_dir() / "workspace-activity"
    marker = directory / f"use-{os.getpid()}-{uuid4()}"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Publish before looking for moves. A mover checks for users only
    # after publishing its own marker. No file handle stays open.
    marker.touch(exist_ok=False)
    with ExitStack() as cleanup:
        cleanup.callback(marker.unlink, missing_ok=True)
        for other in directory.iterdir():
            match = _marker.fullmatch(other.name)
            if match is None:
                continue
            if not psutil.pid_exists(int(match[2])):
                other.unlink(missing_ok=True)
            elif match[1] == "move":
                raise WGSExtractError(
                    "The workspace is being moved. Try again when the move has finished."
                )
        return cleanup.pop_all()
