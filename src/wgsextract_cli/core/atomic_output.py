"""Publish completed outputs without replacing previous files on failure."""

import contextlib
import logging
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from pathlib import Path

from wgsextract_cli.core.paths import PathInput

_enabled: ContextVar[bool | None] = ContextVar("atomic_outputs", default=None)


def atomic_outputs_enabled() -> bool:
    from wgsextract_cli.core.config import settings

    override = _enabled.get()
    return bool(settings.get("atomic_outputs", False)) if override is None else override


@contextlib.contextmanager
def atomic_output_mode(enabled: bool) -> Iterator[None]:
    token = _enabled.set(enabled)
    try:
        yield
    finally:
        _enabled.reset(token)


@contextlib.contextmanager
def safe_output(path: PathInput) -> Iterator[str]:
    if atomic_outputs_enabled():
        with atomic_output(path) as staged:
            yield staged
    else:
        yield str(path)


def _indexes(path: Path) -> list[Path]:
    indexes = [
        Path(str(path) + ext) for ext in (".bai", ".crai", ".csi", ".tbi", ".idx")
    ]
    alternate = {".bam": ".bai", ".cram": ".crai"}.get(path.suffix.lower())
    if alternate:
        indexes.append(path.with_suffix(alternate))
    return indexes


@contextlib.contextmanager
def atomic_output(path: PathInput, *, retire_indexes: bool = True) -> Iterator[str]:
    final = Path(path)
    suffix = "".join(final.suffixes)
    base = final.name[: -len(suffix)] if suffix else final.name
    staged = final.with_name(f".{base}.{uuid.uuid4().hex}.partial{suffix}")
    retired: list[tuple[Path, Path]] = []
    published: list[Path] = []
    previous = Path(f"{staged}.previous")
    replaced = False
    try:
        yield str(staged)
        if not staged.is_file():
            from wgsextract_cli.core.utils import WGSExtractError

            raise WGSExtractError(f"Output writer did not produce {final}")
        try:
            for number, index in enumerate(_indexes(final) if retire_indexes else []):
                if index.exists() or index.is_symlink():
                    backup = Path(f"{staged}.previous-{number}{index.suffix}")
                    os.replace(index, backup)
                    retired.append((index, backup))
            # An index the writer made as it wrote (samtools --write-index) is
            # published after the data, so a crash can leave the new data
            # unindexed but never an index beside data it does not describe.
            written = [
                ext
                for ext in (".bai", ".crai", ".csi", ".tbi")
                if Path(str(staged) + ext).is_file()
            ]
            if written and final.is_file():
                with contextlib.suppress(OSError):
                    os.link(final, previous)
            os.replace(staged, final)
            replaced = True
            for ext in written:
                os.replace(str(staged) + ext, str(final) + ext)
                published.append(Path(str(final) + ext))
        except BaseException:
            for index in published:
                index.unlink(missing_ok=True)
            if replaced and previous.exists():
                os.replace(previous, final)
            elif replaced:
                # The old data is gone, so its indexes would describe the new.
                for _, backup in retired:
                    backup.unlink(missing_ok=True)
                retired.clear()
            for index, backup in retired:
                os.replace(backup, index)
            raise
        else:
            for _, backup in retired:
                backup.unlink(missing_ok=True)
    finally:
        staged.unlink(missing_ok=True)
        previous.unlink(missing_ok=True)
        for index in _indexes(staged):
            index.unlink(missing_ok=True)


def atomic_write(path: PathInput, data: bytes, *, new_mode: int = 0o600) -> None:
    """Replace ``path`` with ``data`` atomically. An existing file keeps its
    permissions; a new one gets ``new_mode`` (private by default; 0o666 gives
    the umask's ordinary permissions)."""
    final = Path(path)
    mode = stat.S_IMODE(final.stat().st_mode) if final.exists() else new_mode
    with atomic_output(final, retire_indexes=False) as staged:
        fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def publish_output_set(staged_to_final: Mapping[Path, Path]) -> None:
    """Publish validated files staged on the destination volume.

    All destinations must share a parent. List the primary file last so it
    is absent while its companions are being replaced.
    """
    from wgsextract_cli.core.file_lock import exclusive_lock

    files = list(staged_to_final.items())
    if files:
        parent = files[-1][1].parent
        with exclusive_lock(parent / ".wgse_publish"):
            _publish_output_set(files, parent)


def _publish_output_set(files: list[tuple[Path, Path]], parent: Path) -> None:
    from wgsextract_cli.core.utils import WGSExtractError

    existing: list[Path] = []
    for staged, final in files:
        if not staged.is_file() or not staged.stat().st_size:
            raise WGSExtractError(f"Output writer did not produce a nonempty {final}")
        try:
            mode = final.lstat().st_mode
        except FileNotFoundError:
            continue
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            raise WGSExtractError(
                f"Cannot replace {final}: not a regular file or symlink"
            )
        if stat.S_ISLNK(mode):
            try:
                mode = final.stat().st_mode
            except FileNotFoundError:
                pass
        if stat.S_ISREG(mode):
            staged.chmod(mode & 0o777)
        existing.append(final)

    backup_dir = Path(tempfile.mkdtemp(dir=parent, prefix=".wgse_previous_"))
    retired: list[tuple[Path, Path]] = []
    publishing: list[tuple[Path, Path]] = []
    try:
        for number, final in enumerate(reversed(existing)):
            backup = backup_dir / str(number)
            retired.append((final, backup))
            final.replace(backup)
        for staged, final in files:
            publishing.append((staged, final))
            staged.replace(final)
    except BaseException:
        try:
            # Cancellation can arrive just after a rename returns.
            for staged, final in reversed(publishing):
                if not staged.exists():
                    final.unlink(missing_ok=True)
            for final, backup in reversed(retired):
                if os.path.lexists(backup):
                    backup.replace(final)
        except OSError as error:
            raise WGSExtractError(
                f"Could not restore previous outputs; remaining backups are in {backup_dir}"
            ) from error
        backup_dir.rmdir()
        raise
    else:
        try:
            shutil.rmtree(backup_dir)
        except OSError as error:
            logging.warning(
                "Could not remove previous outputs in %s: %s", backup_dir, error
            )
