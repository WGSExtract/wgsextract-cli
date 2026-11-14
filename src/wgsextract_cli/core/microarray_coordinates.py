"""Cache exact reverse-chain projections, never inferred forward-chain inverses.

A site is lifted only when the chain places it exactly once on a primary
chromosome and no other source site lands on the same position; every other
site is dropped (a no-call in vendor files) and counted by reason."""

from __future__ import annotations

import hashlib
import logging
import os
import struct
import tempfile
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any, NamedTuple

from pyliftover import LiftOver

from wgsextract_cli.core.file_lock import build_lock
from wgsextract_cli.core.library_cache import microarray_cache_root, short_key

# Codes double as the output sort order, matching legacy chr_to_int(): MT before X/Y.
_CHROMS = (*(f"chr{i}" for i in range(1, 23)), "chrM", "chrX", "chrY")
_CODES = {chrom: code for code, chrom in enumerate(_CHROMS)}
_MAGIC = b"WGSHG19\x03"
_HEADER = struct.Struct("<8sIIIII")
_ROW = struct.Struct("<IBQB")


class Projection(NamedTuple):
    source_index: int
    chrom: str
    position: int
    inverted: bool


class Placement(NamedTuple):
    chrom: str
    position: int  # 0-based, as pyliftover reports it
    strand: str


class LiftoverCounts(NamedTuple):
    """Source sites by outcome. ``ambiguous``: the chain places the site more
    than once; ``collision``: distinct source sites land on one position."""

    lifted: int = 0
    unmapped: int = 0
    alt_contig: int = 0
    ambiguous: int = 0
    collision: int = 0


def sole_placement(
    hits: Iterable[Sequence[Any]] | None,
) -> Placement | str:
    """The single placement among pyliftover hits, else why there is none:
    ``"unmapped"`` or ``"ambiguous"``. Never picks one of several."""
    placements = {Placement(*hit[:3]) for hit in hits or ()}
    if not placements:
        return "unmapped"
    if len(placements) > 1:
        return "ambiguous"
    return placements.pop()


def _read(path: Path, count: int) -> tuple[Iterator[Projection], LiftoverCounts] | None:
    try:
        with path.open("rb") as source:
            data = source.read(_HEADER.size + count * _ROW.size + 33)
        if len(data) < _HEADER.size + 32:
            return None
        payload, digest = data[:-32], data[-32:]
        if hashlib.sha256(payload).digest() != digest:
            return None
        magic, *fields = _HEADER.unpack_from(payload)
        counts = LiftoverCounts(*fields)
        rows = counts.lifted
        if (
            magic != _MAGIC
            or sum(counts) != count
            or len(payload) != _HEADER.size + rows * _ROW.size
        ):
            return None
        return (
            (
                Projection(index, _CHROMS[code], position, bool(inverted))
                for index, code, position, inverted in _ROW.iter_unpack(
                    memoryview(payload)[_HEADER.size :]
                )
            ),
            counts,
        )
    except (OSError, ValueError, struct.error):
        return None


def _write(path: Path, rows: list[Projection], counts: LiftoverCounts) -> None:
    payload = bytearray(_HEADER.pack(_MAGIC, *counts))
    for row in rows:
        payload.extend(
            _ROW.pack(row.source_index, _CODES[row.chrom], row.position, row.inverted)
        )
    digest = hashlib.sha256(payload).digest()
    with tempfile.TemporaryDirectory(dir=path.parent, prefix="coordinates-") as work:
        staged = Path(work, path.name)
        with staged.open("wb") as destination:
            destination.write(payload)
            destination.write(digest)
        os.replace(staged, path)


def _project(
    coordinates: Sequence[tuple[str, int]], chain: str
) -> tuple[list[Projection], LiftoverCounts]:
    lifter = LiftOver(chain)
    rows = []
    reasons: Counter[str] = Counter()
    for index, (chrom, position) in enumerate(coordinates):
        try:
            placement = sole_placement(lifter.convert_coordinate(chrom, position - 1))
        except (ValueError, TypeError):
            placement = "unmapped"
        if isinstance(placement, str):
            reasons[placement] += 1
        elif placement.chrom not in _CODES:
            reasons["alt_contig"] += 1
        else:
            rows.append(
                Projection(
                    index,
                    placement.chrom,
                    placement.position + 1,
                    placement.strand == "-",
                )
            )
    # Repeated rows of one source site may share a target; distinct sites may not.
    first: dict[tuple[str, int], tuple[str, int]] = {}
    clashing = set()
    for row in rows:
        target, source = (row.chrom, row.position), coordinates[row.source_index]
        if first.setdefault(target, source) != source:
            clashing.add(target)
    kept = [row for row in rows if (row.chrom, row.position) not in clashing]
    kept.sort(key=lambda row: (_CODES[row.chrom], row.position))
    return kept, LiftoverCounts(
        lifted=len(kept),
        unmapped=reasons["unmapped"],
        alt_contig=reasons["alt_contig"],
        ambiguous=reasons["ambiguous"],
        collision=len(rows) - len(kept),
    )


def project_coordinates(
    coordinates: Sequence[tuple[str, int]],
    chain: str,
    library: str | None = None,
) -> tuple[Iterator[Projection], LiftoverCounts]:
    root = microarray_cache_root(library)
    if root is None:
        rows, counts = _project(coordinates, chain)
        return iter(rows), counts
    chain_digest = hashlib.sha256()
    with Path(chain).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            chain_digest.update(chunk)
    digest = hashlib.sha256(_MAGIC + chain_digest.digest())
    for chrom, position in coordinates:
        digest.update(f"{chrom}\t{position}\n".encode())
    directory = root / "hg19-coordinates"
    path = directory / f"{short_key(digest)}.bin"
    cached = _read(path, len(coordinates))
    if cached is not None:
        return cached
    with ExitStack() as stack:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stack.enter_context(build_lock(str(path) + ".lock"))
        except OSError as error:
            logging.warning("Microarray coordinate cache unavailable: %s", error)
            rows, counts = _project(coordinates, chain)
            return iter(rows), counts
        cached = _read(path, len(coordinates))
        if cached is not None:
            return cached
        rows, counts = _project(coordinates, chain)
        try:
            _write(path, rows, counts)
        except OSError as error:
            logging.warning("Could not save microarray coordinate cache: %s", error)
    return iter(rows), counts
