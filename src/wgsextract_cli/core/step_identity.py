"""Identities of the files and folders a step reads and writes (#1181).

A file's identity is its size, mtime and a digest of one of two strengths:
``full`` (SHA-256 of the whole file, for files up to ``FULL_DIGEST_LIMIT``,
recomputed at every check) or ``sampled`` (``results.fingerprint``'s first,
middle and last MiB, for larger files, trusted only while the mtime is
unchanged). A directory's identity hashes every file's relative path, size
and mtime (``metadata``) without reading any file. See docs/step-resume.md.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import os
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from wgsextract_cli.core.step_documents import Digest, DirectoryIdentity, FileIdentity

DIRECTORY_FINGERPRINT_PREFIX = "wgse-dir1:"
# Files up to this size are hashed in full at every check (~40 ms at 64 MiB);
# larger ones by size, mtime and sampled blocks.
FULL_DIGEST_LIMIT = 64 << 20


def _fingerprint(path: Path) -> str | None:
    from wgsextract_cli.core.sample_workspace import file_fingerprint

    return file_fingerprint(path)


@functools.lru_cache(maxsize=256)
def _sha256(path: str, size: int, mtime_ns: int, ctime_ns: int, inode: int) -> str:
    # Keyed by ctime too, which no one can set back: an edit that restores
    # the size and mtime is still hashed again.
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(4 << 20):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def file_identity(path: Path) -> FileIdentity:
    """A file's size, mtime and fingerprint: a full SHA-256 of files up to
    ``FULL_DIGEST_LIMIT``, sampled blocks (``results.fingerprint``) of larger
    ones, which are then trusted only while their mtime is unchanged."""
    info = path.stat()
    digest: Digest
    if info.st_size <= FULL_DIGEST_LIMIT:
        fingerprint: str | None = _sha256(
            str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino
        )
        digest = {"algorithm": "sha256", "strength": "full"}
    else:
        fingerprint = _fingerprint(path)
        digest = {"algorithm": "wgse-fp1", "strength": "sampled"}
    return {
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "fingerprint": fingerprint,
        "digest": digest,
    }


def file_drift(old: Mapping[str, Any], new: Mapping[str, Any]) -> str | None:
    """Why a file no longer matches its record (``"changed ..."``), or None."""
    if not old.get("digest"):
        return "was recorded by an earlier wgsextract without an integrity digest"
    if old.get("size") != new.get("size"):
        return (
            f"changed size ({old.get('size')} -> {new.get('size')} bytes; "
            "truncated or rewritten)"
        )
    if old.get("fingerprint") != new.get("fingerprint"):
        algorithm = new["digest"]["algorithm"]
        return f"changed ({algorithm} {new['digest']['strength']} digest differs)"
    if new["digest"]["strength"] == "sampled" and old.get("mtime_ns") != new.get(
        "mtime_ns"
    ):
        return (
            "was modified (its mtime changed, and a file over "
            f"{FULL_DIGEST_LIMIT >> 20} MiB is checked by size, mtime and "
            "sampled blocks, not a full read)"
        )
    return None


METADATA_DIGEST: Digest = {"algorithm": "wgse-dir1", "strength": "metadata"}


def input_drift(old: Mapping[str, Any], new: Mapping[str, Any]) -> str | None:
    """Why an input no longer matches its record, or None."""
    if new.get("missing"):
        return "is missing"
    if old.get("kind") == "directory" or new.get("kind") == "directory":
        if old.get("kind") == "directory" and not old.get("fingerprint"):
            return "is a directory whose contents an earlier wgsextract didn't record"
        return None if old.get("fingerprint") == new.get("fingerprint") else "changed"
    if "digest" in new:
        return file_drift(old, new)
    # A URL, identified by what its server reports.
    return None if old.get("fingerprint") == new.get("fingerprint") else "changed"


def strengths(identities: Sequence[Mapping[str, Any]]) -> str:
    """``2 sha256 full, 1 wgse-fp1 sampled``: how a skipped step was checked."""
    counts: dict[str, int] = {}
    for identity in identities:
        digest = identity.get("digest")
        if digest:
            label = f"{digest['algorithm']} {digest['strength']}"
            counts[label] = counts.get(label, 0) + 1
    return ", ".join(f"{n} {label}" for label, n in sorted(counts.items()))


def _transient(name: str) -> bool:
    """Lock files and staged partial outputs, which come and go while a
    directory's data stays the same."""
    return name.endswith((".lock", ".guard")) or (
        name.startswith(".") and ".partial" in name
    )


def _name_bytes(text: str) -> bytes:
    # File names that aren't valid UTF-8 reach Python as lone surrogates.
    return text.encode("utf-8", "surrogateescape")


def _link_target(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return "?"


def directory_identity(root: Path) -> DirectoryIdentity:
    """The identity of a directory's contents, without reading any file.

    Hashes each file's path relative to ``root``, size and mtime (ns), in
    sorted order, so adding, removing, renaming, resizing or rewriting a file
    changes it. Symbolic links are followed (each directory once), and their
    targets hashed too, so repointing one changes it. A folder that can't be
    listed, ``root`` included, is hashed as unreadable. Costs one stat per
    file, so a VEP cache of tens of thousands of files takes well under a
    second.
    """
    digest = hashlib.sha256(b"wgse-dir1\0")
    files = total = 0
    seen: set[tuple[int, int]] = set()
    pending = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            info = directory.stat()
            if (info.st_dev, info.st_ino) in seen:
                continue
            seen.add((info.st_dev, info.st_ino))
            with os.scandir(directory) as listing:
                entries = sorted(listing, key=lambda entry: entry.name)
        except OSError:
            digest.update(_name_bytes(f"{prefix}\0unreadable\n"))
            continue
        subdirectories = []
        for entry in entries:
            if _transient(entry.name):
                continue
            name = prefix + entry.name
            with contextlib.suppress(OSError):
                if entry.is_symlink():
                    target = _link_target(entry.path)
                    digest.update(_name_bytes(f"{name}\0link\0{target}\n"))
            try:
                info = entry.stat()
            except OSError:
                digest.update(_name_bytes(f"{name}\0unreadable\n"))
                continue
            if stat.S_ISDIR(info.st_mode):
                subdirectories.append((Path(entry.path), name + "/"))
            elif stat.S_ISREG(info.st_mode):
                files += 1
                total += info.st_size
                line = f"{name}\0{info.st_size}\0{info.st_mtime_ns}\n"
                digest.update(_name_bytes(line))
        # Depth first, in name order: the same tree always hashes the same.
        pending.extend(reversed(subdirectories))
    return {
        "files": files,
        "size": total,
        "fingerprint": DIRECTORY_FINGERPRINT_PREFIX + digest.hexdigest(),
    }


def directory_drift(
    path: Path, item: Mapping[str, Any], checked: list[Mapping[str, Any]]
) -> str | None:
    """Why a directory output no longer holds what the step wrote: one of
    its declared ``members`` changed, or (without declared members) any
    file in it but locks and staged partials was added, removed or
    rewritten."""
    if not path.is_dir():
        return f"{path.name} is no longer a directory"
    if not item.get("digest"):
        return (
            f"{path.name} is a directory whose contents an earlier wgsextract "
            "didn't record"
        )
    members = item.get("members")
    if members is not None:
        for member, old in sorted(members.items()):
            file = path / member
            if not file.is_file():
                return f"{path.name}/{member}, a required member, is missing"
            identity = file_identity(file)
            checked.append(identity)
            if drift := file_drift(old, identity):
                return f"{path.name}/{member}, a required member, {drift}"
        return None
    now = directory_identity(path)
    checked.append({**now, "digest": METADATA_DIGEST})
    if now["fingerprint"] != item.get("fingerprint"):
        return (
            f"{path.name}'s contents changed ({item.get('files')} files, "
            f"{item.get('size')} bytes when written; now {now['files']} files, "
            f"{now['size']} bytes)"
        )
    return None
