"""Read both sides of a test-selection diff from immutable Git objects."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChangedPath:
    status: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class ImpactDiff:
    base: str
    head: str
    changes: tuple[ChangedPath, ...]


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root)


def _commit(root: Path, ref: str) -> str:
    return (
        _git(root, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
        .decode()
        .strip()
    )


def impact_diff(root: Path, base: str, head: str) -> ImpactDiff:
    """Keep the removed and added sides, including paths that contain tabs."""
    head_sha = _commit(root, head)
    base_sha = _git(root, "merge-base", _commit(root, base), head_sha).decode().strip()
    diff = _git(
        root, "diff", "--name-status", "-z", "--find-renames", base_sha, head_sha, "--"
    )
    entries = iter(diff.split(b"\0")[:-1])
    changes: list[ChangedPath] = []
    for token in entries:
        path = os.fsdecode(next(entries))
        status = chr(token[0])
        match status:
            case "A":
                changes.append(ChangedPath(status, None, path))
            case "D":
                changes.append(ChangedPath(status, path, None))
            case "R":
                changes.append(ChangedPath(status, path, os.fsdecode(next(entries))))
            case "C":
                changes.append(ChangedPath(status, None, os.fsdecode(next(entries))))
            case _:
                changes.append(ChangedPath(status, path, path))
    return ImpactDiff(base_sha, head_sha, tuple(changes))


def git_blob(root: Path, commit: str, path: str) -> bytes:
    """Return a tracked file version without consulting the working tree."""
    return _git(root, "cat-file", "blob", f"{_commit(root, commit)}:{path}")
