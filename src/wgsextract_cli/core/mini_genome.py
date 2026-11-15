"""The test genome: the small hg19 benchmark dataset (1000 Genomes HG00096,
three short regions) published as a release asset, which the app offers to
try WGS Extract on. It installs into the dataset library as
``wgsextract-benchmark-hg19-mini/`` with a ``genome-config.toml``, so
``--genome wgsextract-benchmark-hg19-mini`` finds its BAM, VCF and FASTQs;
the verified zip stays in the library's ``.downloads`` folder."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from wgsextract_cli.core.genome_library import GENOME_CONFIG_NAME, write_genome_config
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.utils import WGSExtractError

DATASET_ID = "wgsextract-benchmark-hg19-mini"
URL = (
    "https://github.com/WGSExtract/wgsextract-cli/releases/download/v0.1.0/"
    f"{DATASET_ID}.zip"
)
SHA256 = "ad0f8070dc5ca35c4a6de540493a81df082d160417f747ae68d9c098c110a9f6"

# Manifest file roles that become genome-config.toml keys.
_CONFIG_ROLES = {
    "alignment": ("bam", "cram"),
    "vcf": ("vcf",),
    "fastq_r1": ("fastq_r1",),
    "fastq_r2": ("fastq_r2",),
}


def folder(datasets: Path) -> Path:
    return datasets / DATASET_ID


def downloads(datasets: Path) -> Path:
    return datasets / ".downloads"


def archive(datasets: Path) -> Path:
    return downloads(datasets) / f"{DATASET_ID}.zip"


def staging(datasets: Path) -> Path:
    """Where an install assembles the dataset before moving it into place."""
    return datasets / f".{DATASET_ID}.installing"


def status(datasets: Path) -> dict[str, Any]:
    """``installed`` (its folder has a genome-config.toml), ``status``
    (``installed``, ``incomplete`` while a download is partial, else
    ``missing``) and ``path``, the dataset's folder."""
    path = folder(datasets)
    installed = (path / GENOME_CONFIG_NAME).is_file()
    if installed:
        state = "installed"
    elif Path(f"{archive(datasets)}.partial").is_file():
        state = "incomplete"
    else:
        state = "missing"
    return {"installed": installed, "status": state, "path": str(path)}


def missing_files(path: Path) -> list[str]:
    """The files the installed dataset's manifest lists that aren't there."""
    try:
        files = json.loads((path / "manifest.json").read_text(encoding="utf-8"))[
            "files"
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return ["manifest.json"]
    if not isinstance(files, dict):
        return ["manifest.json"]
    return [
        name
        for name in files.values()
        if isinstance(name, str) and name and not (path / name).is_file()
    ]


def install(datasets: Path) -> bool:
    """Download (or reuse the verified zip), check its SHA-256 and unpack the
    dataset into ``datasets``; False when it was already complete."""
    target = folder(datasets)
    if status(datasets)["installed"]:
        missing = missing_files(target)
        if not missing:
            logging.info(f"The test genome is already installed at {target}.")
            return False
        logging.warning(
            f"The test genome is missing {', '.join(missing)}; reinstalling."
        )
    zip_path = _verified_archive(datasets)
    with tempfile.TemporaryDirectory(
        prefix=f"{DATASET_ID}-", dir=downloads(datasets)
    ) as tmp:
        extracted = Path(tmp, "extract")
        extract_zip_safely(zip_path, extracted)
        payload = _payload_root(extracted)
        _write_config(payload)
        assembled = staging(datasets)
        if assembled.exists():
            shutil.rmtree(assembled)
        shutil.move(str(payload), assembled)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)
    assembled.replace(target)
    logging.info(f"Installed the test genome at {target}.")
    return True


def _verified_archive(datasets: Path) -> Path:
    zip_path = archive(datasets)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.is_file() and _sha256(zip_path) == SHA256:
        logging.info(f"Using the downloaded {zip_path}.")
        return zip_path
    zip_path.unlink(missing_ok=True)
    if not download_file(
        URL, str(zip_path), expected_sha256=SHA256, resolve_release_digest=False
    ):
        raise WGSExtractError(
            f"Could not download the test genome from {URL} (a download error "
            "or a SHA-256 mismatch); run again to resume."
        )
    return zip_path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_zip_safely(zip_path: Path, extract_dir: Path) -> None:
    """Unpack ``zip_path`` into ``extract_dir``, refusing links and entries
    that would land outside it."""
    extract_dir.mkdir(parents=True, exist_ok=True)
    root = extract_dir.resolve()
    try:
        with zipfile.ZipFile(zip_path) as zipped:
            for member in zipped.infolist():
                target = (extract_dir / member.filename).resolve()
                linked = stat.S_ISLNK(member.external_attr >> 16)
                if linked or not target.is_relative_to(root):
                    raise WGSExtractError(
                        f"Unsafe dataset zip entry: {member.filename}"
                    )
            zipped.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise WGSExtractError(f"Invalid dataset zip: {zip_path}") from exc


def _payload_root(extracted: Path) -> Path:
    """The folder holding the dataset's manifest.json: the zip's root or its
    one top folder."""
    if (extracted / "manifest.json").is_file():
        return extracted
    found = list(extracted.glob("*/manifest.json"))
    if len(found) == 1:
        return found[0].parent
    raise WGSExtractError("The test genome zip has no manifest.json.")


def _write_config(path: Path) -> None:
    files = json.loads((path / "manifest.json").read_text(encoding="utf-8")).get(
        "files"
    )
    if not isinstance(files, dict):
        raise WGSExtractError("The test genome's manifest.json lists no files.")
    config = {}
    for key, roles in _CONFIG_ROLES.items():
        name = next((files[r] for r in roles if isinstance(files.get(r), str)), "")
        if name:
            config[key] = name
    write_genome_config(path / GENOME_CONFIG_NAME, config)
