"""Command-scoped sample workspaces and fact discovery and recording."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterable, Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

from wgsextract_cli.core.paths import PathInput, absolute_path
from wgsextract_cli.core.sample_facts import Fact
from wgsextract_cli.core.sample_types import (
    Confidence,
    FactName,
    ObservedPlatform,
    ObservedSex,
    Sex,
    Superpopulation,
)
from wgsextract_cli.core.sample_workspace_format import WORKSPACE_FILE

if TYPE_CHECKING:
    from wgsextract_cli.core.sample_workspace import SampleWorkspace

# The running command's --genome workspace and the files it claims; per
# context, so worker threads (core.execution_context) see their command's.
_active: ContextVar[tuple[SampleWorkspace, frozenset[Path]] | None] = ContextVar(
    "active_sample_workspace", default=None
)


@contextlib.contextmanager
def active_workspace(
    folder: PathInput | None, files: Iterable[PathInput | None] = ()
) -> Iterator[None]:
    """Make ``folder`` (a --genome folder) the workspace of the files in it and
    of ``files`` (its inputs, which genome-config.toml may place elsewhere)."""
    if folder is None:
        yield
        return
    # sample_workspace exposes these command helpers at its existing import path.
    from wgsextract_cli.core.sample_workspace import SampleWorkspace

    token = _active.set(
        (SampleWorkspace(folder), frozenset(absolute_path(f) for f in files if f))
    )
    try:
        yield
    finally:
        _active.reset(token)


def current_workspace() -> SampleWorkspace | None:
    """The --genome workspace of the running command, if any."""
    active = _active.get()
    return active[0] if active else None


def workspace_for(file: PathInput | None) -> SampleWorkspace | None:
    """The workspace ``file`` belongs to: the --genome folder for its files and
    inputs, else the file's own folder when that is a sample or genome folder."""
    if not file:
        return None
    path = absolute_path(file)
    active = _active.get()
    if active is not None and (
        path in active[1] or path.is_relative_to(active[0].folder)
    ):
        return active[0]
    from wgsextract_cli.core.genome_library import GENOME_CONFIG_NAME
    from wgsextract_cli.core.sample_workspace import SampleWorkspace

    folder = path.parent
    if (folder / WORKSPACE_FILE).is_file() or (folder / GENOME_CONFIG_NAME).is_file():
        return SampleWorkspace(folder)
    return None


_LABELS = {
    "sex": "Sex",
    "platform": "Platform",
    "build": "Build",
    "somalier_sex": "Sex (somalier)",
    "superpopulation": "Superpopulation",
    "read_length": "Read length",
    "mean_coverage": "Mean coverage",
    "target_depths": "Target depths",
    "contamination": "Contamination",
    "ancestry": "Ancestry (K=7)",
}
_reported: set[tuple[str, str]] = set()


def known(name: str, file: PathInput | None) -> Fact | None:
    """The fact in effect for ``file``, if its workspace has one: the user's
    override, else one detected from this revision of the file by the current
    detector. Logged once per run."""
    workspace = workspace_for(file)
    if workspace is None or file is None:
        return None
    try:
        fact = workspace.fact(name, file)
    except (OSError, ValueError) as e:
        logging.debug(f"Couldn't read sample facts from {workspace.path}: {e}")
        return None
    if fact is not None and (name, workspace.key(file)) not in _reported:
        _reported.add((name, workspace.key(file)))
        logging.info(f"{_LABELS.get(name, name)}: {fact.value} ({fact.describe()})")
    return fact


_recording: ContextVar[bool] = ContextVar("sample_facts_recording", default=True)


@contextlib.contextmanager
def recording_paused() -> Iterator[None]:
    """Detect facts without recording them, for a report that changes nothing."""
    token = _recording.set(False)
    try:
        yield
    finally:
        _recording.reset(token)


@overload
def record(
    name: Literal["sex"],
    file: PathInput | None,
    value: ObservedSex | None,
    *,
    method: str,
    confidence: Confidence,
) -> bool: ...


@overload
def record(
    name: Literal["somalier_sex"],
    file: PathInput | None,
    value: Sex | None,
    *,
    method: str,
    confidence: Confidence,
) -> bool: ...


@overload
def record(
    name: Literal["platform"],
    file: PathInput | None,
    value: ObservedPlatform | None,
    *,
    method: str,
    confidence: Confidence,
    statistics: dict[str, float] | None = None,
) -> bool: ...


@overload
def record(
    name: Literal["superpopulation"],
    file: PathInput | None,
    value: Superpopulation | None,
    *,
    method: str,
    confidence: Confidence,
) -> bool: ...


@overload
def record(
    name: Literal["build"],
    file: PathInput | None,
    value: str | None,
    *,
    method: str,
    confidence: Confidence,
) -> bool: ...


@overload
def record(
    name: Literal["read_length", "mean_coverage", "contamination"],
    file: PathInput | None,
    value: float | None,
    *,
    method: str,
    confidence: Confidence,
) -> bool: ...


@overload
def record(
    name: Literal["target_depths", "ancestry"],
    file: PathInput | None,
    value: dict[str, float] | None,
    *,
    method: str,
    confidence: Confidence,
) -> bool: ...


def record(
    name: FactName,
    file: PathInput | None,
    value: object,
    *,
    method: str,
    confidence: Confidence,
    statistics: dict[str, float] | None = None,
) -> bool:
    """Record a detected fact in ``file``'s workspace, if it has one, and warn
    if it contradicts the user's override."""
    workspace = workspace_for(file)
    if workspace is None or file is None or not _recording.get():
        return False
    recorded = workspace._record(
        name, file, value, method=method, confidence=confidence, statistics=statistics
    )
    check_override(name, file, value, method)
    return recorded


_checked: set[tuple[str, str]] = set()


def check_override(
    name: str, file: PathInput | None, detected: object, method: str | None
) -> None:
    """Warn, into the result manifest, when the reads contradict the override
    in effect for ``file``: it changes the algorithm, so a wrong one must not
    go unnoticed. Once per fact and file per run."""
    workspace = workspace_for(file)
    if workspace is None or file is None or detected in (None, "Unknown", "unknown"):
        return
    try:
        override = workspace.overrides(file).get(name)
    except (OSError, ValueError):
        return
    key = (name, str(absolute_path(file)))
    if override is None or override == detected or key in _checked:
        return
    _checked.add(key)
    from wgsextract_cli.core.results import warn

    how = f" ({method})" if method else ""
    warn(
        f"{name}_override_differs",
        f"Using your {name} override {override} for {Path(file).name}, but its "
        f"reads look {detected}{how}. If the override is wrong, remove it with "
        f"'wgsextract sample unset --{name}'.",
        file=workspace.key(file),
        detected=str(detected),
        override=override,
    )


def resolve(
    name: str, file: PathInput | None, detect: Callable[[], tuple[Any, str | None]]
) -> Any:
    """The value of a fact that changes an algorithm: the recorded or
    detected one, or the user's override. With an override the fact is still
    detected (``detect`` returns (value, method) and records it) or read from
    the record, so a contradicting override is warned about."""
    fact = known(name, file)
    if fact is not None and fact.source == "detected":
        return fact.value
    workspace = workspace_for(file) if fact is not None else None
    recorded = workspace.detected(name, file) if workspace and file else None
    value, method = (recorded.value, recorded.method) if recorded else detect()
    if fact is None:
        return value
    check_override(name, file, value, method)
    return fact.value
