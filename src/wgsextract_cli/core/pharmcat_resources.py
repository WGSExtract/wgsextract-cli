"""Verified, transactional installation of PharmCAT's public GRCh38 reference."""

from __future__ import annotations

import logging
import shutil
import tarfile
import tempfile
from pathlib import Path

from wgsextract_cli.core.network_policy import require_download
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.utils import WGSExtractError

REFERENCE_FASTA = "reference.fna.bgz"
REFERENCE_URL = "https://zenodo.org/records/7288118/files/GRCh38_reference_fasta.tar"
# Zenodo record 7288118: 882785724 bytes, MD5 a4047175ae90e2df36900f039f1cf260.
REFERENCE_SHA256 = "7aeb9c9b7ddb262aab32fe97a48317a4a54d97680dea27b81af4561cf502f839"
REFERENCE_FILES = (REFERENCE_FASTA + ".fai", REFERENCE_FASTA + ".gzi", REFERENCE_FASTA)


def ensure_reference(directory: str) -> str:
    """Keep installed references offline; publish the FASTA only after its indexes."""
    root = Path(directory)
    if all(
        (root / name).is_file() and (root / name).stat().st_size
        for name in REFERENCE_FILES
    ):
        return str(root / REFERENCE_FASTA)
    require_download("PharmCAT GRCh38 reference.fna.bgz (Zenodo record 7288118)")
    logging.info("Fetching PharmCAT's verified GRCh38 reference (about 1 GB, once)...")
    with tempfile.TemporaryDirectory(prefix=".pharmcat-reference-", dir=root) as work:
        staging = Path(work)
        archive = staging / "GRCh38_reference_fasta.tar"
        for attempt in range(2):
            if download_file(
                REFERENCE_URL, str(archive), expected_sha256=REFERENCE_SHA256
            ):
                break
            if attempt == 1:
                raise WGSExtractError(
                    "Could not download and verify PharmCAT's GRCh38 reference. "
                    "Rerun with --download to retry."
                )
            logging.warning(
                "PharmCAT reference download failed verification; refetching."
            )
        try:
            with tarfile.open(archive, "r:*") as bundle:
                for name in REFERENCE_FILES:
                    member = bundle.getmember(name)
                    if not member.isfile() or not member.size:
                        raise ValueError(f"Missing regular reference file: {name}")
                    source = bundle.extractfile(member)
                    if source is None:
                        raise ValueError(f"Unreadable reference file: {name}")
                    with source, (staging / name).open("wb") as target:
                        shutil.copyfileobj(source, target)
        except (tarfile.TarError, EOFError, KeyError, ValueError) as exc:
            raise WGSExtractError(f"Invalid PharmCAT reference archive: {exc}") from exc
        # A previous upstream download may have left a truncated FASTA. Never
        # let that look complete while the replacement indexes are published.
        (root / REFERENCE_FASTA).unlink(missing_ok=True)
        for name in REFERENCE_FILES:
            (staging / name).replace(root / name)
    return str(root / REFERENCE_FASTA)
