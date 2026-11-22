"""What a BAM, CRAM, VCF or BCF says about itself: its header's assembly
and samples, and whether its index is ready (#1182). Nothing here runs a
tool; headers and indexes are read directly, and only as far as needed."""

from __future__ import annotations

import functools
import gzip
import logging
import os
import re
import struct
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from wgsextract_cli.core.output_schema import Constraints

GENOMIC_KINDS = frozenset({"bam", "cram", "vcf", "bcf"})
IndexState = Literal["ready", "missing", "stale", "mismatched"]
_HEADER_LIMIT = 64 << 20
# A VCF may declare a contig without its length (bcftools adds such lines).
_CONTIG_ID = re.compile(r"^##contig=<ID=([^,>]+)", re.MULTILINE)


@dataclass
class ArtifactIndex:
    path: str | None
    state: Annotated[
        IndexState,
        Constraints(
            description="ready; missing; stale (older than the file and pointing "
            "at blocks it no longer has); mismatched (another file's index)."
        ),
    ]


def _bgzf_record(path: Path, magic: bytes) -> str | None:
    with gzip.open(path, "rb") as stream:
        if stream.read(len(magic)) != magic:
            return None
        (length,) = struct.unpack("<i", stream.read(4))
        if not 0 <= length <= _HEADER_LIMIT:
            return None
        return stream.read(length).decode("utf-8", "replace").rstrip("\0")


def _vcf_header(path: Path) -> str | None:
    lines: list[str] = []
    size = 0
    with path.open("rb") as probe:
        gzipped = probe.read(2) == b"\x1f\x8b"
    opener = gzip.open if gzipped else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if not line.startswith("#"):
                break
            lines.append(line)
            size += len(line)
            if line.startswith("#CHROM") or size > _HEADER_LIMIT:
                break
    return "".join(lines) or None


def read_header(path: str | os.PathLike[str], kind: str) -> str | None:
    """The SAM-style header of a BAM or BCF, or a VCF's header lines; None
    when it can't be read here (CRAM, a stream, a damaged file)."""
    file = Path(path)
    try:
        if kind == "bam":
            return _bgzf_record(file, b"BAM\x01")
        if kind == "bcf":
            return _bgzf_record(file, b"BCF\x02\x02")
        if kind == "vcf":
            return _vcf_header(file)
    except (OSError, EOFError, ValueError, struct.error, zlib.error):
        logging.debug(f"Unreadable header in {file}", exc_info=True)
    return None


def header_lengths(header: str) -> dict[str, int]:
    from wgsextract_cli.core.ref_library.resolver import header_contig_lengths

    return header_contig_lengths(header)


def header_samples(header: str, kind: str) -> list[str]:
    if kind in ("bam", "cram"):
        from wgsextract_cli.core.ultima import read_groups

        return sorted({g["SM"] for g in read_groups(header) if g.get("SM")})
    for line in header.splitlines():
        if line.startswith("#CHROM"):
            return line.rstrip("\r\n").split("\t")[9:]
    return []


def assembly_of(lengths: Mapping[str, int]) -> str | None:
    from wgsextract_cli.core.builds import build_from_chr1_length

    return build_from_chr1_length(dict(lengths))


def index_paths(path: str | os.PathLike[str], kind: str) -> list[str]:
    """Every index a reader may use, in htslib's order: a CSI before the
    format's own (.bai, .crai or .tbi), each as ``<file><ext>`` or with the
    file's extension replaced."""
    file = Path(path)
    own = {"bam": ".bai", "cram": ".crai", "vcf": ".tbi"}.get(kind)
    found: list[str] = []
    for suffix in (".csi", *([own] if own else [])):
        for candidate in (Path(f"{file}{suffix}"), file.with_suffix(suffix)):
            if (
                candidate != file
                and candidate.is_file()
                and str(candidate) not in found
            ):
                found.append(str(candidate))
    return found


@dataclass
class _IndexContents:
    count: int
    names: list[str] | None  # TBI and VCF CSI store them; BAI doesn't
    offsets: list[int]  # file offsets of the BGZF blocks its chunks point at


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.at = data, 0

    def take(self, fmt: str) -> tuple[int, ...]:
        values = struct.unpack_from(fmt, self.data, self.at)
        self.at += struct.calcsize(fmt)
        return values

    def bins(self, count: int, csi: bool, pseudo: int, offsets: set[int]) -> None:
        for _ in range(count):
            (n_bin,) = self.take("<i")
            for _ in range(n_bin):
                (bin_id,) = self.take("<I")
                if csi:
                    self.take("<Q")  # loffset
                (n_chunk,) = self.take("<i")
                chunks = self.take(f"<{2 * n_chunk}Q")
                if bin_id != pseudo:  # its "chunks" are read counts
                    offsets.update(v >> 16 for v in chunks)
            if not csi:
                (n_intv,) = self.take("<i")
                self.at += 8 * n_intv


def _names(raw: bytes) -> list[str]:
    return [n.decode() for n in raw.split(b"\0") if n]


def _index_contents(index: Path) -> _IndexContents | None:
    """What a BAI, TBI or CSI records; None for another format (CRAI)."""
    data = index.read_bytes()
    if not data.startswith(b"BAI\x01"):
        data = gzip.decompress(data)
    reader = _Reader(data)
    magic = data[:4]
    reader.at = 4
    offsets: set[int] = set()
    names: list[str] | None = None
    if magic == b"BAI\x01":
        (count,) = reader.take("<i")
        reader.bins(count, False, 37450, offsets)
    elif magic == b"TBI\x01":
        count, *_meta, names_length = reader.take("<8i")
        names = _names(data[reader.at : reader.at + names_length])
        reader.at += names_length
        reader.bins(count, False, 37450, offsets)
    elif magic == b"CSI\x01":
        _shift, depth, aux_length = reader.take("<3i")
        aux = data[reader.at : reader.at + aux_length]
        reader.at += aux_length
        if len(aux) >= 28:
            (names_length,) = struct.unpack_from("<i", aux, 24)
            names = _names(aux[28 : 28 + names_length])
        (count,) = reader.take("<i")
        reader.bins(count, True, ((1 << (3 * depth + 3)) - 1) // 7 + 1, offsets)
    else:
        return None
    return _IndexContents(count, names, sorted(offsets))


def _blocks_exist(file: Path, offsets: Sequence[int], sample: int = 64) -> bool:
    """Whether the BGZF blocks an index points at are in ``file`` (a sample
    spread over it, and the last): an index of another version of the file
    points into other blocks, or past its end."""
    size = file.stat().st_size
    step = max(1, len(offsets) // sample)
    with file.open("rb") as stream:
        for offset in sorted({*offsets[::step], *offsets[-1:]}):
            if offset == size:
                continue
            stream.seek(offset)
            if offset > size or stream.read(4) != b"\x1f\x8b\x08\x04":
                return False
    return True


def index_state(
    path: str | os.PathLike[str], kind: str, header: str | None
) -> ArtifactIndex | None:
    """The state of a file's indexes; None for a file that can't have one
    (an uncompressed VCF). Each index a reader may pick is checked, and the
    first that is unusable is reported; else the one htslib picks.

    ``stale``: older than the file, and pointing at blocks the file no longer
    has. An older index whose blocks are all there is the file's own:
    downloads and copies often leave the index the older file. ``mismatched``:
    another sequence dictionary, blocks that aren't the file's though the
    index is newer, or an unreadable index. A CRAI isn't read: only its
    presence is checked."""
    file = Path(path)
    if kind == "vcf" and not file.name.lower().endswith((".gz", ".bgz")):
        return None
    found = index_paths(file, kind)
    if not found:
        return ArtifactIndex(None, "missing")
    for index in found:
        state = _index_file_state(file, Path(index), kind, header)
        if state != "ready":
            return ArtifactIndex(index, state)
    return ArtifactIndex(found[0], "ready")


def _index_file_state(
    file: Path, index: Path, kind: str, header: str | None
) -> IndexState:
    try:
        contents = _index_contents(index)
        if contents is None:
            return "ready" if index.suffix == ".crai" else "mismatched"
        older = index.stat().st_mtime_ns < file.stat().st_mtime_ns
        blocks = _blocks_exist(file, contents.offsets)
    except (OSError, EOFError, ValueError, struct.error, zlib.error):
        return "mismatched"
    if header is not None:
        contigs = header_lengths(header)
        # A BAI/CSI of a BAM, or a BCF's CSI, counts every header contig.
        if kind in ("bam", "cram", "bcf") and contents.count != len(contigs):
            return "mismatched"
        names = set(contents.names or ())
        declared = set(contigs) | set(_CONTIG_ID.findall(header))
        if declared and not names <= declared:
            return "mismatched"
    if not blocks:
        return "stale" if older else "mismatched"
    return "ready"


@dataclass
class FileFacts:
    header: str | None
    assembly: str | None
    samples: list[str]
    index: ArtifactIndex | None


def file_facts(path: str, kind: str) -> FileFacts:
    """A file's facts, read once per version of it and its index (a
    consumer's check and the manifest both need them)."""
    try:
        info = os.stat(path)
        indexes = tuple((i, os.stat(i).st_mtime_ns) for i in index_paths(path, kind))
    except OSError:
        return _read_facts(path, kind)
    return _cached_facts(path, kind, info.st_mtime_ns, info.st_size, indexes)


@functools.lru_cache(maxsize=64)
def _cached_facts(path: str, kind: str, *_version: object) -> FileFacts:
    return _read_facts(path, kind)


def _read_facts(path: str, kind: str) -> FileFacts:
    header = read_header(path, kind)
    return FileFacts(
        header,
        assembly_of(header_lengths(header)) if header else None,
        header_samples(header, kind) if header else [],
        index_state(path, kind, header),
    )
