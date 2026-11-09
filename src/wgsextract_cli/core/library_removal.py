"""Deleting what the ``ref`` and ``vep`` installers put in a reference library.

Each ``*_paths`` function lists what one installer leaves: its files, their
indexes and an interrupted download's leftovers. ``remove`` deletes them, and
then the folders the installer made, once they are empty.
"""

import logging
import shutil
from collections.abc import Iterable, Iterator
from pathlib import Path

from wgsextract_cli.core.aadr_resources import aadr_2m_path
from wgsextract_cli.core.reference_assets import annotation_vcf_names
from wgsextract_cli.core.results import manifest_path_for
from wgsextract_cli.core.utils import WGSExtractError

_INDEXES = (".tbi", ".csi")
_DOWNLOAD_LEFTOVERS = (".partial", ".partial.segments", ".partial.source")

# The files `ref status` finds for each deletable annotation resource:
# <key>*<extension> in the library or its ref folder.
_RESOURCE_EXTENSIONS = {
    "spliceai": (".vcf.gz", ".vcf.bgz"),
    "alphamissense": (".tsv.gz", ".vcf.gz", ".vcf.bgz"),
    "pharmgkb": (".tsv.gz", ".vcf.gz", ".vcf.bgz"),
}


def _with_sidecars(path: Path) -> Iterator[Path]:
    """``path``, its indexes, its run manifest and download leftovers."""
    yield path
    for suffix in (*_INDEXES, *_DOWNLOAD_LEFTOVERS):
        yield Path(f"{path}{suffix}")
    yield manifest_path_for(path)


def aadr_paths(reflib: str) -> list[Path]:
    return list(_with_sidecars(aadr_2m_path(reflib)))


def ybrowse_paths(reflib: str) -> list[Path]:
    """Every installed snapshot, with the pointer to the current one."""
    return [Path(reflib, "ybrowse", "hg38")]


def annotation_resource_paths(reflib: str, key: str) -> list[Path]:
    extensions = _RESOURCE_EXTENSIONS[key]
    found = {
        path
        for directory in (Path(reflib), Path(reflib, "ref"))
        if directory.is_dir()
        for extension in extensions
        for path in directory.glob(f"{key}*{extension}")
        if path.is_file()
    }
    return [sidecar for path in sorted(found) for sidecar in _with_sidecars(path)]


def microarray_target_paths(reflib: str) -> list[Path]:
    """The genome-wide targets `microarray` builds (and older bootstraps
    shipped): All_SNPs_<build>_ref.tab.gz wherever `ref status` looks."""
    names = [n for n in annotation_vcf_names("") if n.startswith("All_SNPs_")]
    directories = (
        Path(reflib),
        Path(reflib, "ref"),
        Path(reflib, "microarray"),
        Path(reflib, "genomes", "microarray"),
    )
    return [
        sidecar
        for directory in directories
        for name in names
        for sidecar in (
            *_with_sidecars(directory / name),
            directory / f"{name}.lock",
            directory / f"{name}.lock.guard",
        )
    ]


def vep_release_paths(
    cache_root: Path, species: str, release: str, assembly: str
) -> list[Path]:
    """One release and assembly of the cache, and its tarball if a download
    of it was left in the cache folder."""
    tarball = cache_root / f"{species}_vep_{release}_{assembly}.tar.gz"
    return [
        cache_root / species / f"{release}_{assembly}",
        *_with_sidecars(tarball),
        Path(f"{tarball}.CHECKSUMS"),
    ]


def remove(paths: Iterable[Path], made_folders: Iterable[Path] = ()) -> list[Path]:
    """Delete each existing file or folder of ``paths``, then each of
    ``made_folders`` that is left empty; return what was deleted."""
    removed = []
    try:
        for path in paths:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.is_file() or path.is_symlink():
                path.unlink()
            else:
                continue
            logging.info(f"Deleted {path}")
            removed.append(path)
        for folder in made_folders:
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
    except OSError as e:
        raise WGSExtractError(f"Could not delete {e.filename}: {e.strerror}") from e
    return removed
