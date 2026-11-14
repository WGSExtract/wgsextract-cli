# SPDX-License-Identifier: GPL-3.0-or-later
# Data names derived from genid/Yleaf v4.1.6, yleaf/yleaf_constants.py
# (commit recorded in provenance.json).
# Copyright Arwin Ralf, Diego Montiel Gonzalez, Kaiyin Zhong, Manfred Kayser
# and Bram van Wersch, Erasmus MC Department of Genetic Identification.
# Modified 2026-11-01 by WGS Extract contributors: pinned release cache.
"""Download only the selected build's tables and the prediction trees."""

import hashlib
import json
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from wgsextract_cli.core.file_lock import build_lock
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.reference_assets import resolve_reference_library
from wgsextract_cli.core.utils import WGSExtractError

UCSC_README = "UCSC-liftOver-README.txt"
"""UCSC's liftOver README, verbatim: its chain files' licence, which must stay in
the same directory as any redistributed chain."""


def ucsc_chain_terms(relative: str) -> dict[str, str]:
    """The ``third_party`` manifest record of a UCSC chain file in an archive."""
    name = Path(relative).name
    folder = (
        f"https://hgdownload.soe.ucsc.edu/goldenPath/{name.split('To', 1)[0]}/liftOver"
    )
    return {
        "source": f"{folder}/{name}",
        "license": (
            "UCSC Chain Files: free for non-commercial use by independent "
            "researchers and nonprofit organizations; any other use needs a "
            "commercial licence from UCSC"
        ),
        "terms": f"{folder}/README.txt",
        "notice": UCSC_README,
    }


def manifest() -> dict[str, Any]:
    with (
        Path(__file__).with_name("data_manifest.json").open(encoding="utf-8") as stream
    ):
        result: dict[str, Any] = json.load(stream)
    return result


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified(root: Path, files: dict[str, Any]) -> bool:
    for name, metadata in files.items():
        path = root / name
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != metadata["size"]
            or file_sha256(path) != metadata["sha256"]
        ):
            return False
    return True


def extract_archive(archive: Path, root: Path, files: dict[str, Any]) -> None:
    """Extract only manifest-listed regular files; never follow archive paths."""
    with tarfile.open(archive, "r:gz") as stream:
        seen = set()
        for entry in stream:
            if (
                not entry.isfile()
                or entry.name not in files
                or entry.name in seen
                or Path(entry.name).is_absolute()
                or ".." in Path(entry.name).parts
                or entry.size != files[entry.name]["size"]
            ):
                raise WGSExtractError(
                    "Unsafe or unexpected file in Yleaf data archive."
                )
            seen.add(entry.name)
            source = stream.extractfile(entry)
            if source is None:
                raise WGSExtractError("Unreadable Yleaf data archive member.")
            target = root / entry.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)
        if seen != set(files):
            raise WGSExtractError("Incomplete Yleaf data archive.")
    if not verified(root, files):
        raise WGSExtractError("Yleaf data failed extracted-file SHA-256 verification.")


def ensure_data(build: str | None, library: Path | None = None) -> Path:
    """Yleaf's prediction tables, plus ``build``'s marker tables unless None."""
    if build not in (None, "hg19", "hg38", "t2t"):
        raise WGSExtractError(f"Unsupported Yleaf build: {build}")
    metadata = manifest()
    root: Path = (
        (library or Path(resolve_reference_library()))
        / "yleaf"
        / str(metadata["upstream"]["tag"])
    )
    root.mkdir(parents=True, exist_ok=True)
    with build_lock(str(root / "data.lock")):
        for key in (*([build] if build else []), "hg_prediction_tables"):
            asset = metadata["archives"][key]
            if verified(root, asset["files"]):
                continue
            logging.info(
                "Downloading Yleaf %s tables (%s)...", key, metadata["upstream"]["tag"]
            )
            archive = root / Path(asset["url"]).name
            try:
                if not download_file(
                    asset["url"],
                    str(archive),
                    expected_sha256=asset["sha256"],
                    resolve_release_digest=False,
                ):
                    raise WGSExtractError(
                        f"Cannot download verified Yleaf {key} data. If offline, "
                        "connect once to populate the configured reference_library."
                    )
                with tempfile.TemporaryDirectory(prefix="unpack-", dir=root) as staging:
                    stage = Path(staging)
                    extract_archive(archive, stage, asset["files"])
                    # Individual atomic replaces are protected by the library lock.
                    for name in asset["files"]:
                        target = root / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        (stage / name).replace(target)
            except (OSError, tarfile.TarError) as error:
                raise WGSExtractError(
                    f"Unable to prepare Yleaf {key} data: {error}"
                ) from error
            finally:
                archive.unlink(missing_ok=True)
        _place_notices(root, metadata.get("third_party", {}))
    return root


def _place_notices(root: Path, third_party: dict[str, Any]) -> None:
    """Put each installed third-party file's licence notice beside it."""
    for name, terms in third_party.items():
        if not (root / name).is_file():
            continue
        notice = Path(__file__).with_name(terms["notice"]).read_bytes()
        target = (root / name).parent / terms["notice"]
        if not target.is_file() or target.read_bytes() != notice:
            target.write_bytes(notice)
