"""Deleting what the ``ref`` and ``vep`` installers put in a reference library,
and the test genome ``ref test-genome`` puts in the dataset library.

Each ``*_paths`` function lists what one installer leaves: its files, their
indexes and an interrupted download's leftovers. ``remove`` deletes them, and
then the folders the installer made, once they are empty.
"""

import contextlib
import logging
import os
import shutil
from collections.abc import Iterable, Iterator
from glob import escape
from pathlib import Path, PureWindowsPath

from wgsextract_cli.core import mini_genome
from wgsextract_cli.core.aadr_resources import aadr_2m_path
from wgsextract_cli.core.file_lock import exclusive_lock, held
from wgsextract_cli.core.ref_library.assets import (
    GENOME_SIDECARS,
    annotation_vcf_names,
    reference_dict_path,
)
from wgsextract_cli.core.ref_library.layout import download_dirs
from wgsextract_cli.core.results import manifest_path_for
from wgsextract_cli.core.utils import UsageError, WGSExtractError

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
        for directory in (Path(reflib), *download_dirs(reflib))
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
        *download_dirs(reflib),
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


# The build_locks of runs that write beside a genome's FASTA: bwa and
# bwa-mem2 indexing, and `ref dense-sa`.
_GENOME_BUILD_LOCKS = (".bwa-index.lock", ".dense-sa.lock")


def genome_paths(fasta: Path) -> list[Path]:
    """What a genome leaves beside its FASTA: the FASTA, a partial download,
    its indexes (samtools, bwa, bwa-mem2 and wgse-bwa's dense suffix arrays),
    its sequence dictionary and their run manifests, and an interrupted index
    build's staging folders. Not its N counts, which the library bootstrap
    ships for every catalog genome."""
    made = [
        fasta,
        *(Path(f"{fasta}{ext}") for ext in GENOME_SIDECARS),
        Path(reference_dict_path(fasta)),
    ]
    name = escape(fasta.name)
    return list(
        dict.fromkeys(
            [
                *made,
                *map(manifest_path_for, made),
                *sorted(fasta.parent.glob(f"{name}.wgse-sa*")),
                *_staging_folders(fasta),
            ]
        )
    )


def _staging_folders(fasta: Path) -> list[Path]:
    """The folders bwa and bwa-mem2 index builds stage in beside ``fasta``."""
    name = escape(fasta.name)
    return sorted(
        path
        for pattern in (
            f".{name}.bwa-index-*",
            f".{name}.bwa-mem2-index-*",
            f".{name}.bwa-mem2-download",
        )  # fmt: skip
        for path in fasta.parent.glob(pattern)
        if path.is_dir() and not path.is_symlink()
    )


def _size(path: Path) -> int:
    """The bytes deleting ``path`` frees: a link's are its target's to keep."""
    if path.is_symlink():
        return 0
    if path.is_dir():
        return sum(p.lstat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size


def remove_genome(folder: Path, name: str) -> dict[Path, int]:
    """Delete genome file ``name`` and what it left in the genomes ``folder``
    (``genome_paths``); return each deleted path with the bytes it freed.

    Only entries of ``folder`` itself are deleted, a link as the link. A
    folder among them other than an index build's staging folder, or an index
    another run is building, is refused before anything is deleted."""
    plain = name == Path(name).name == PureWindowsPath(name).name
    if not plain or name in ("", ".", ".."):
        raise UsageError(f"{name!r} is not a genome file name.")
    if not folder.is_dir():
        return {}
    fasta = Path(os.path.realpath(folder), name)
    locks = [Path(f"{fasta}{suffix}") for suffix in _GENOME_BUILD_LOCKS]
    guards = [Path(f"{lock}.guard") for lock in locks]
    ours = [guard for guard in guards if not guard.exists()]
    deleted = False
    try:
        with contextlib.ExitStack() as stack:
            # Holding each lock's guard keeps a builder from starting meanwhile.
            for lock in locks:
                stack.enter_context(exclusive_lock(lock, timeout=10))
                if held(str(lock)):
                    raise WGSExtractError(
                        f"Another run is building {lock.name.removesuffix('.lock')}"
                        f" ({lock}); delete {name} once it has finished."
                    )
            found = [p for p in genome_paths(fasta) if p.exists() or p.is_symlink()]
            staging = set(_staging_folders(fasta))
            for path in found:
                if path.is_dir() and not path.is_symlink() and path not in staging:
                    raise WGSExtractError(f"{path} is a folder; not deleting {name}.")
            sizes = {path: _size(path) for path in found}
            removed = remove([*found, *locks])
            deleted = True
    finally:
        # Guards outlive their locks, so they go once released: all of them
        # with the genome (a builder starting now has no FASTA to index), else
        # only those this call made.
        for guard in guards if deleted else ours:
            guard.unlink(missing_ok=True)
    return {path: sizes[path] for path in removed if path in sizes}


def remove_test_genome(datasets: Path) -> dict[Path, int]:
    """Delete the test genome's folder, its zip with any partial download of
    it and an interrupted install; then ``.downloads`` if left empty. Return
    each deleted path with the bytes it freed; a link goes as the link."""
    zip_path = mini_genome.archive(datasets)
    found = [
        path
        for path in (
            mini_genome.folder(datasets),
            mini_genome.staging(datasets),
            *_with_sidecars(zip_path),
        )
        if path.exists() or path.is_symlink()
    ]
    sizes = {path: _size(path) for path in found}
    removed = remove(found, [mini_genome.downloads(datasets)])
    return {path: sizes[path] for path in removed}


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
