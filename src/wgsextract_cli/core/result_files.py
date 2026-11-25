"""File records and content fingerprints used by result manifests."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from wgsextract_cli.core.artifacts import ArtifactRecord
from wgsextract_cli.core.output_schema import Constraints
from wgsextract_cli.core.provenance_records import Fingerprint
from wgsextract_cli.core.result_warnings import Scalar

FINGERPRINT_PREFIX = "wgse-fp1:"
_FINGERPRINT_BLOCK = 1 << 20  # 1 MiB

# Longest suffixes first so ".vcf.gz" wins over ".gz".
_KINDS: tuple[tuple[str, str], ...] = (
    (".vcf.gz", "vcf"),
    (".vcf.bgz", "vcf"),
    (".fastq.gz", "fastq"),
    (".fq.gz", "fastq"),
    (".fasta.gz", "fasta"),
    (".fa.gz", "fasta"),
    (".fna.gz", "fasta"),
    (".bed.gz", "bed"),
    (".tsv.gz", "tsv"),
    (".csv.gz", "csv"),
    (".txt.gz", "txt"),
    (".bam", "bam"),
    (".cram", "cram"),
    (".sam", "sam"),
    (".vcf", "vcf"),
    (".bcf", "bcf"),
    (".gvcf", "vcf"),
    (".fasta", "fasta"),
    (".fa", "fasta"),
    (".fna", "fasta"),
    (".fastq", "fastq"),
    (".fq", "fastq"),
    (".bed", "bed"),
    (".tsv", "tsv"),
    (".csv", "csv"),
    (".txt", "txt"),
    (".json", "json"),
    (".html", "html"),
    (".pdf", "pdf"),
    (".zip", "zip"),
    (".sqlite", "sqlite"),
    (".parquet", "parquet"),
    (".gz", "gzip"),
)
_INDEX_SUFFIXES = {
    "bam": (".bai", ".csi"),
    "cram": (".crai",),
    "vcf": (".tbi", ".csi"),
    "bcf": (".csi",),
    "fasta": (".fai",),
}


def _utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def file_kind(path: str | os.PathLike[str]) -> str:
    """The file type a manifest reports, from the name (or ``directory``)."""
    name = os.fspath(path).lower()
    if os.path.isdir(name) or os.path.isdir(os.fspath(path)):
        return "directory"
    for suffix, kind in _KINDS:
        if name.endswith(suffix):
            return kind
    return "other"


def fingerprint(path: str | os.PathLike[str]) -> str | None:
    """A cheap content fingerprint, ``wgse-fp1:<sha256 hex>``.

    Hashes the size and the first, middle and last MiB (the whole file when it
    is at most 3 MiB), so a 100 GB BAM costs three reads, not a full pass. It
    tells files apart and survives copies (mtime isn't hashed), but it isn't an
    integrity check: an edit confined to unread bytes keeps the fingerprint.
    Use ``wgsextract sign`` for full-content digests. None for unreadable
    files and anything but a regular file: opening a FIFO, ``/dev/stdin`` or a
    process substitution would block or drain the input the command needs.
    """
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        with open(path, "rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            digest = hashlib.sha256(b"wgse-fp1\0" + str(size).encode() + b"\0")
            if size <= 3 * _FINGERPRINT_BLOCK:
                digest.update(stream.read())
            else:
                for offset in (
                    0,
                    (size - _FINGERPRINT_BLOCK) // 2,
                    size - _FINGERPRINT_BLOCK,
                ):
                    stream.seek(offset)
                    digest.update(stream.read(_FINGERPRINT_BLOCK))
    except (OSError, ValueError):
        return None
    return FINGERPRINT_PREFIX + digest.hexdigest()


def _index_of(path: Path, kind: str) -> str | None:
    for suffix in _INDEX_SUFFIXES.get(kind, ()):
        for candidate in (Path(str(path) + suffix), path.with_suffix(suffix)):
            if candidate != path and candidate.is_file():
                return str(candidate)
    return None


@dataclass
class FileRecord:
    """A file (or directory) a command read or wrote."""

    path: Annotated[str, Constraints(description="Absolute path.")]
    kind: Annotated[
        str,
        Constraints(
            description="File type from the name: bam, cram, vcf, fasta, fastq, bed, tsv, ..., directory or other."
        ),
    ]
    role: Annotated[
        str,
        Constraints(
            description="What the file is to this command, e.g. input, snps, combined_kit."
        ),
    ]
    exists: bool = False
    size_bytes: Annotated[int | None, Constraints(minimum=0)] = None
    mtime: Annotated[str | None, Constraints(format="date-time")] = None
    fingerprint: Fingerprint = None
    index: Annotated[
        str | None,
        Constraints(
            description="Absolute path of the file's index (.bai, .crai, .tbi, .csi, .fai), if any."
        ),
    ] = None
    metrics: dict[str, Scalar] = field(default_factory=dict)
    artifact: Annotated[
        ArtifactRecord | None,
        Constraints(
            description="What a BAM, CRAM, VCF or BCF is and where it came from; "
            "absent for other files."
        ),
    ] = field(default=None, metadata={"omit_none": True})

    @classmethod
    def describe(
        cls,
        path: str | os.PathLike[str],
        role: str,
        *,
        kind: str | None = None,
        metrics: Mapping[str, Scalar] | None = None,
    ) -> FileRecord:
        from wgsextract_cli.core.remote_input import is_url, remote_file

        if is_url(path):
            remote = remote_file(os.fspath(path))
            return cls(
                path=remote.url,
                kind=kind or file_kind(Path(urlsplit(remote.url).path)),
                role=role,
                exists=True,
                size_bytes=remote.size,
                fingerprint=remote.fingerprint,
                metrics=dict(metrics or {}),
            )
        absolute = Path(path).expanduser().absolute()
        record = cls(
            path=str(absolute),
            kind=kind or file_kind(absolute),
            role=role,
            metrics=dict(metrics or {}),
        )
        record.refresh()
        return record

    def refresh(self) -> None:
        """Re-read size, mtime, fingerprint and index from disk.

        Only regular files are opened; a FIFO or device (``/dev/stdin``, a
        process substitution) is recorded without size or fingerprint. A
        URL keeps what its HEAD request said."""
        if "://" in self.path:
            return
        path = Path(self.path)
        try:
            info = path.stat()
        except OSError:
            self.exists, self.size_bytes, self.mtime = False, None, None
            self.fingerprint = self.index = None
            return
        self.exists = True
        self.mtime = _utc(info.st_mtime)
        if stat.S_ISDIR(info.st_mode):
            self.kind = "directory"
        if not stat.S_ISREG(info.st_mode):
            self.size_bytes = self.fingerprint = self.index = None
            return
        self.size_bytes = info.st_size
        self.fingerprint = fingerprint(path)
        self.index = _index_of(path, self.kind)
