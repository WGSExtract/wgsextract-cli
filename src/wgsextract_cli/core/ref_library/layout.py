"""Where a reference library keeps the annotation data ``ref`` commands download.

New downloads go in ``downloads/``. Older libraries kept them in ``ref/``,
which is still read, so they keep working unchanged.
"""

from pathlib import Path

from wgsextract_cli.core.paths import PathInput

DOWNLOADS_DIR = "downloads"
LEGACY_DOWNLOADS_DIR = "ref"
DOWNLOAD_DIRS = (DOWNLOADS_DIR, LEGACY_DOWNLOADS_DIR)


def downloads_dir(reflib: PathInput) -> Path:
    """The folder new downloads go in."""
    return Path(reflib, DOWNLOADS_DIR)


def download_dirs(reflib: PathInput) -> list[Path]:
    """Every folder downloads are read from, in search order."""
    return [Path(reflib, name) for name in DOWNLOAD_DIRS]


def download_path(reflib: PathInput, *parts: str) -> Path:
    """A downloaded file or folder: in ``downloads/``, unless only an older
    library's ``ref/`` holds it or its interrupted ``.partial`` download."""
    current, legacy = (Path(d, *parts) for d in download_dirs(reflib))
    return legacy if _started(legacy) and not _started(current) else current


def _started(path: Path) -> bool:
    return path.exists() or Path(f"{path}.partial").exists()
