"""Publish completed outputs without replacing previous files on failure."""

import contextlib
import errno
import hashlib
import logging
import os
import shutil
import stat
import tempfile
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from pathlib import Path

from wgsextract_cli.core.paths import PathInput

_enabled: ContextVar[bool | None] = ContextVar("atomic_outputs", default=None)
_publication_locks: ContextVar[contextlib.ExitStack | None] = ContextVar(
    "publication_locks", default=None
)


@contextlib.contextmanager
def output_publication_scope() -> Iterator[None]:
    """Retain output-set locks until this invocation writes its result manifest."""
    with contextlib.ExitStack() as locks:
        token = _publication_locks.set(locks)
        try:
            yield
        finally:
            _publication_locks.reset(token)


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


def publish_output_set(
    staged_to_final: Mapping[Path, Path],
    *,
    checkpoint: Callable[[], None] | None = None,
) -> None:
    """Publish validated files staged on the destination volume.

    All destinations must share a parent. List the primary file last so it
    is absent while its companions are being replaced.
    """
    from wgsextract_cli.core.file_lock import exclusive_lock

    files = list(staged_to_final.items())
    if files:
        primary = files[-1][1]
        # Normalize case and Unicode for output directories on insensitive filesystems.
        name = unicodedata.normalize("NFC", primary.name.casefold())
        key = hashlib.sha256(os.fsencode(name)).hexdigest()[:32]
        lock = exclusive_lock(
            primary.parent / f".wgse_publish-{key}", checkpoint=checkpoint
        )
        with contextlib.ExitStack() as local:
            scope = _publication_locks.get()
            (local if scope is None else scope).enter_context(lock)
            _publish_output_set(files, primary.parent, checkpoint)


def _preserve_output_permissions(
    final: Path, staged: Path, info: os.stat_result
) -> None:
    current = staged.stat()
    if hasattr(os, "chown") and (info.st_uid, info.st_gid) != (
        current.st_uid,
        current.st_gid,
    ):
        os.chown(staged, info.st_uid, info.st_gid)
    staged.chmod(info.st_mode & 0o777)
    if hasattr(os, "getxattr"):
        acl_name = "system.posix_acl_access"
        try:
            acl = os.getxattr(final, acl_name)
        except OSError as error:
            if error.errno not in {errno.ENODATA, errno.ENOTSUP}:
                raise
            try:
                os.removexattr(staged, acl_name)
            except OSError as staged_error:
                if staged_error.errno not in {errno.ENODATA, errno.ENOTSUP}:
                    raise
        else:
            os.setxattr(staged, acl_name, acl)


def _publish_output_set(
    files: list[tuple[Path, Path]], parent: Path, checkpoint: Callable[[], None] | None
) -> None:
    from wgsextract_cli.core.utils import WGSExtractError

    existing: list[Path] = []
    for staged, final in files:
        if not staged.is_file() or not staged.stat().st_size:
            raise WGSExtractError(f"Output writer did not produce a nonempty {final}")
        try:
            info = final.lstat()
        except FileNotFoundError:
            continue
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            raise WGSExtractError(
                f"Cannot replace {final}: not a regular file or symlink"
            )
        if stat.S_ISLNK(info.st_mode):
            try:
                info = final.stat()
            except FileNotFoundError:
                pass
        if stat.S_ISREG(info.st_mode):
            _preserve_output_permissions(final, staged, info)
        existing.append(final)

    if checkpoint is not None:
        checkpoint()
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
