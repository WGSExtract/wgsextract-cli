"""Shared path authorization for reference-library operations."""

import os
from collections.abc import Iterator
from pathlib import Path

from ._params import Params, with_config_defaults


def _library_named(ref: Path) -> Path:
    """The library named by a directory or a FASTA inside its genomes folder."""
    from wgsextract_cli.core.reference_assets import reference_library_of

    # Lexically, without absolutising, so a Windows request is checked as such.
    ref = Path(os.path.normpath(ref.expanduser()))
    return Path(reference_library_of(ref)) if ref.is_file() else ref


class _InLibrary:
    """An operation on the library ``ref`` names, or the one above a FASTA's
    ``genomes`` folder."""

    ref: Path | None

    def library(self) -> Path:
        """The library folder the operation installs into or reads."""
        from wgsextract_cli.core.reference_assets import (
            reference_library_of,
            resolve_reference_library,
        )

        ref = reference_library_of(self.ref) if self.ref else None
        return Path(resolve_reference_library(ref))

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        if self.ref is not None:
            yield "ref", _library_named(self.ref)

    def write_folders(self) -> Iterator[Path]:
        from wgsextract_cli.core.reference_assets import reference_genomes_dir

        library = self.library()
        yield from (library, Path(reference_genomes_dir(library)))


class _ConfiguredLibrary(_InLibrary, Params):
    """An operation on the library ``ref`` names, else the configured FASTA's."""

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        # Unset, ref is the configured FASTA's library, else reference_library.
        filled = with_config_defaults(self)
        if filled.ref is not None:
            if self.ref is None:
                yield "ref", filled.ref.expanduser()
            yield "ref", _library_named(filled.ref)
        else:
            yield "ref", self.library()
