"""Verified, transactional installation of PharmCAT's public GRCh38 reference."""

from __future__ import annotations

import logging
import struct
import tarfile
import tempfile
from pathlib import Path

from wgsextract_cli.core.file_lock import exclusive_lock
from wgsextract_cli.core.network_policy import require_download
from wgsextract_cli.core.ref_library.downloads import (
    download_file,
    verify_download_sha256,
)
from wgsextract_cli.core.utils import WGSExtractError

REFERENCE_FASTA = "reference.fna.bgz"
REFERENCE_URL = "https://zenodo.org/records/7288118/files/GRCh38_reference_fasta.tar"
# Zenodo record 7288118: 882785724 bytes, MD5 a4047175ae90e2df36900f039f1cf260.
REFERENCE_SHA256 = "7aeb9c9b7ddb262aab32fe97a48317a4a54d97680dea27b81af4561cf502f839"
REFERENCE_FILES = (REFERENCE_FASTA + ".fai", REFERENCE_FASTA + ".gzi", REFERENCE_FASTA)


def _complete_reference(root: Path) -> bool:
    try:
        fasta = root / REFERENCE_FASTA
        initial = fasta.stat()
        fasta_size = initial.st_size
        rows = (
            (root / (REFERENCE_FASTA + ".fai")).read_text(encoding="ascii").splitlines()
        )
        if not fasta_size or not rows:
            return False
        names: set[str] = set()
        for row in rows:
            fields = row.split("\t")
            if len(fields) != 5 or not fields[0] or fields[0] in names:
                return False
            length, offset, bases, width = map(int, fields[1:])
            if length <= 0 or offset < 0 or not 0 < bases <= width:
                return False
            names.add(fields[0])
        index = root / (REFERENCE_FASTA + ".gzi")
        with index.open("rb") as stream:
            (entries,) = struct.unpack("<Q", stream.read(8))
            if index.stat().st_size != 8 + entries * 16:
                return False
            previous_compressed = previous_uncompressed = 0
            for compressed, uncompressed in struct.iter_unpack("<QQ", stream.read()):
                if not (
                    previous_compressed < compressed < fasta_size
                    and previous_uncompressed < uncompressed
                ):
                    return False
                previous_compressed, previous_uncompressed = compressed, uncompressed
        return fasta.stat() == initial
    except (OSError, UnicodeError, ValueError, struct.error):
        return False


def ensure_reference(directory: str) -> str:
    """Keep installed references offline; publish the FASTA only after its indexes."""
    from wgsextract_cli.api._runtime import cancel_event, checkpoint

    root = Path(directory)
    checkpoint()
    if _complete_reference(root):
        return str(root / REFERENCE_FASTA)
    with exclusive_lock(
        root / ".pharmcat-reference", timeout=3600, checkpoint=checkpoint
    ):
        checkpoint()
        if _complete_reference(root):
            return str(root / REFERENCE_FASTA)
        require_download("PharmCAT GRCh38 reference.fna.bgz (Zenodo record 7288118)")
        logging.info(
            "Fetching PharmCAT's verified GRCh38 reference (about 1 GB, once)..."
        )
        # Retain transport state across cancellation or exhausted retries. Only
        # extracted files are disposable; this directory's lock protects both.
        archive = root / ".pharmcat-reference.tar"
        verified = archive.is_file() and verify_download_sha256(
            str(archive), REFERENCE_SHA256
        )
        checkpoint()
        if not verified:
            for attempt in range(2):
                downloaded = download_file(
                    REFERENCE_URL,
                    str(archive),
                    cancel_event=cancel_event(),
                    expected_sha256=REFERENCE_SHA256,
                )
                checkpoint()
                if downloaded:
                    break
                if attempt == 1:
                    raise WGSExtractError(
                        "Could not download and verify PharmCAT's GRCh38 reference. "
                        "Rerun with --download to retry."
                    )
                logging.warning(
                    "PharmCAT reference download failed verification; refetching."
                )
        with tempfile.TemporaryDirectory(
            prefix=".pharmcat-reference-", dir=root
        ) as work:
            staging = Path(work)
            found: set[str] = set()
            try:
                with tarfile.open(archive, "r|*") as bundle:
                    for member in bundle:
                        checkpoint()
                        name = member.name
                        if name not in REFERENCE_FILES:
                            continue
                        if name in found or not member.isfile() or not member.size:
                            raise ValueError(f"Invalid reference file: {name}")
                        source = bundle.extractfile(member)
                        if source is None:
                            raise ValueError(f"Unreadable reference file: {name}")
                        with source, (staging / name).open("wb") as target:
                            while chunk := source.read(1024 * 1024):
                                checkpoint()
                                target.write(chunk)
                        found.add(name)
                if found != set(REFERENCE_FILES) or not _complete_reference(staging):
                    raise ValueError("Missing or invalid reference indexes")
            except (tarfile.TarError, EOFError, KeyError, ValueError) as exc:
                raise WGSExtractError(
                    f"Invalid PharmCAT reference archive: {exc}"
                ) from exc
            checkpoint()
            # Retire a damaged legacy FASTA, then publish the complete set with
            # the FASTA last. The next lock holder reuses it without replacing it.
            (root / REFERENCE_FASTA).unlink(missing_ok=True)
            for name in REFERENCE_FILES:
                (staging / name).replace(root / name)
        archive.unlink()
    return str(root / REFERENCE_FASTA)
