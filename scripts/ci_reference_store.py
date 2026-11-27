"""Pin, validate and link the host's shared certification reference inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scripts.reference_store_paths import (
    is_store_library,
    registered_worktrees,
    store_path,
)


@dataclass(frozen=True)
class ReferenceStore:
    store: Path
    version: Path
    trees: list[Path]

    @property
    def library(self) -> Path:
        return self.version / "library"

    @property
    def receipt(self) -> Path:
        return self.version / "verified.json"

    @classmethod
    def for_checkout(cls, checkout: Path) -> ReferenceStore:
        from wgsextract_cli.core.constants import BOOTSTRAP_SHA256
        from wgsextract_cli.core.microarray.targets import HG19_TO_HG38_CHAIN

        _, hs38_sha256 = hs38_pin()
        pins = f"1:{BOOTSTRAP_SHA256}:{HG19_TO_HG38_CHAIN.sha256}:{hs38_sha256}"
        trees = registered_worktrees(checkout)
        store = store_path(trees)
        return cls(store, store / hashlib.sha256(pins.encode()).hexdigest(), trees)

    def check_location(self) -> None:
        if (
            self.version.resolve() != self.version
            or self.library.resolve() != self.library
        ):
            raise ValueError(
                f"Reference store must not point outside itself: {self.version}"
            )

    def verified(self) -> bool:
        self.check_location()
        try:
            if self.receipt.is_symlink():
                return False
            files = json.loads(self.receipt.read_text(encoding="utf-8"))
            return (
                isinstance(files, dict)
                and bool(files)
                and all(
                    fingerprint(self.version / name, self.version) == expected
                    for name, expected in files.items()
                )
            )
        except (OSError, ValueError):
            return False

    def record_verified(self, files: dict[Path, list[int]]) -> None:
        from wgsextract_cli.core.atomic_output import atomic_write

        self.check_location()
        for path, expected in files.items():
            if fingerprint(path, self.version) != expected:
                raise ValueError(f"Reference input changed during verification: {path}")
        relative = {
            path.relative_to(self.version).as_posix(): stamp
            for path, stamp in files.items()
        }
        atomic_write(self.receipt, json.dumps(relative).encode())

    def check_destination(self, destination: Path) -> None:
        if destination.resolve() == self.library:
            return
        if not any(destination == tree / "reference" for tree in self.trees):
            raise ValueError(
                f"Refusing to change configured reference library {destination}; "
                f"use the worktree's reference/ or set reference_library to {self.library}"
            )
        if destination.is_symlink():
            if not is_store_library(destination.resolve(), self.store):
                raise ValueError(
                    f"Refusing to replace an unrelated symlink: {destination}"
                )
        elif destination.exists():
            raise ValueError(
                f"Existing reference library left unchanged: {destination}; "
                "move it aside before linking the shared library"
            )

    def link_checkout(self, destination: Path) -> None:
        self.check_destination(destination)
        if destination.resolve() == self.library:
            return
        if destination.is_symlink():
            replace_with_link(destination, self.library, directory=True)
        else:
            destination.symlink_to(self.library, target_is_directory=True)

    def link_existing_hs38(self, destination: Path) -> bool:
        from wgsextract_cli.core.file_lock import exclusive_lock

        self.check_location()
        if not self.version.is_dir():
            raise ValueError("Prepare the pinned shared store before migrating hs38")
        with exclusive_lock(self.version / "setup", timeout=120):
            return self._link_existing_hs38(destination)

    def _link_existing_hs38(self, destination: Path) -> bool:
        from scripts.ci_certify_inputs import digest

        name, expected = hs38_pin()
        source = self.library / "genomes" / name
        if not self.verified() or not source.is_file():
            raise ValueError("Prepare the pinned shared store before migrating hs38")
        source_stamp = fingerprint(source, self.version)
        destination = Path(os.path.abspath(destination))
        checkout = next(
            (
                tree
                for tree in sorted(self.trees[1:], key=lambda p: -len(p.parts))
                if destination.is_relative_to(tree)
            ),
            None,
        )
        if (
            checkout is None
            or destination.name != name
            or destination.parent.resolve() != destination.parent
            or subprocess.run(
                ["git", "-C", str(checkout), "check-ignore", "--", str(destination)],
                check=False,
                stdout=subprocess.DEVNULL,
                timeout=15,
            ).returncode
            != 0
        ):
            raise ValueError(
                f"Not an ignored hs38 FASTA in a registered linked worktree: {destination}"
            )
        if destination.is_symlink() and destination.resolve() == source:
            return False
        before = fingerprint(destination, checkout)
        if before[:2] == source_stamp[:2]:
            return False
        if (
            digest(destination) != expected
            or fingerprint(destination, checkout) != before
        ):
            raise ValueError(
                f"Not a stable byte-identical copy of pinned hs38: {destination}"
            )
        replace_with_link(
            destination,
            source,
            check=lambda: (
                fingerprint(destination, checkout) == before
                and fingerprint(source, self.version) == source_stamp
                and self.verified()
            ),
        )
        return True


def hs38_pin() -> tuple[str, str]:
    from wgsextract_cli.core.ref_library.catalog import get_available_genomes

    for genome in get_available_genomes():
        if genome["code"] == "hs38" and re.fullmatch(
            r"[a-f0-9]{64}", genome.get("sha256", "")
        ):
            return str(genome["final"]), str(genome["sha256"])
    raise ValueError("Cannot prove installed hs38 reference against a pinned catalog")


def fingerprint(path: Path, root: Path) -> list[int]:
    if not path.is_relative_to(root) or path.resolve() != path:
        raise ValueError(
            f"Unexpected symlink or path outside verified reference inputs: {path}"
        )
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Not a regular reference input: {path}")
    # ctime also catches in-place edits whose size and mtime were restored.
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def replace_with_link(
    destination: Path,
    source: Path,
    *,
    directory: bool = False,
    check: Callable[[], bool] | None = None,
) -> None:
    staged = destination.with_name(f".reference-link-{uuid.uuid4().hex}")
    staged.symlink_to(source, target_is_directory=directory)
    try:
        if check is not None and not check():
            raise ValueError(f"Reference changed before replacement: {destination}")
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)
