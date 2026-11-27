"""Locate the host's certification reference store without importing the CLI."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import tomllib


def registered_worktrees(checkout: Path) -> list[Path]:
    listing = subprocess.check_output(
        ["git", "-C", str(checkout), "worktree", "list", "--porcelain", "-z"],
        timeout=15,
    )
    return [
        Path(
            field.removeprefix(b"worktree ").decode("utf-8", "surrogateescape")
        ).resolve()
        for field in listing.split(b"\0")
        if field.startswith(b"worktree ")
    ]


def store_path(worktrees: list[Path]) -> Path:
    main, *linked = worktrees
    shared = main / "out" / "shared"
    config = shared / "config.toml"
    try:
        with config.open("rb") as stream:
            configured = tomllib.load(stream).get("certification_reference_store")
    except FileNotFoundError:
        configured = None
    if configured is None:
        path = shared / "certification-reference"
    elif (
        isinstance(configured, str)
        and (path := Path(configured).expanduser()).is_absolute()
    ):
        pass
    else:
        raise ValueError(
            f"certification_reference_store in {config} must be an absolute path"
        )
    resolved = path.resolve()
    for location in (Path(os.path.abspath(path)), resolved):
        if (
            (location.is_relative_to(main) and not location.is_relative_to(shared))
            or any(
                location.is_relative_to(tree) or tree.is_relative_to(location)
                for tree in linked
            )
            or main.is_relative_to(location)
            or location == shared
            or location == config
        ):
            raise ValueError(
                f"Certification reference store overlaps a checkout or shared configuration: {location}"
            )
    return resolved


def is_store_library(path: Path, store: Path) -> bool:
    return (
        path.name == "library"
        and path.parent.parent == store
        and re.fullmatch(r"[0-9a-f]{64}", path.parent.name) is not None
    )


def worktree_alias(checkout: Path, store: Path) -> Path | None:
    link = checkout / "reference"
    if link.is_symlink():
        target = link.resolve()
        if target.is_dir() and is_store_library(target, store):
            return target
    return None
