"""The pinned rCRS-to-CHM13 mitochondrial bridge of the CHM13 site map."""

from __future__ import annotations

import gzip
import hashlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class MitochondrialBridge:
    edit_distance: int
    source_to_target: tuple[int, ...]
    target_to_source: tuple[int, ...]
    aligned_source: str
    aligned_target: str
    target_rotation: int = 0

    def original_target(self) -> str:
        rotated = self.aligned_target.replace("-", "")
        offset = self.target_rotation
        return rotated[-offset:] + rotated[:-offset] if offset else rotated


def align_mitochondria(
    source: str, target: str, *, band: int = 128, target_rotation: int = 0
) -> MitochondrialBridge:
    """Globally align MT; zero map entries are nonreciprocal/ambiguous/gapped."""
    if not source or not target or set(source + target) - set("ACGTN"):
        raise ValueError("Mitochondrial sequences must be nonempty A/C/G/T/N strings")
    if not 0 <= target_rotation < len(target):
        raise ValueError("Invalid circular target origin")
    target = target[target_rotation:] + target[:target_rotation]
    if not 0 <= band <= 256:
        raise ValueError("Mitochondrial alignment band must be in [0, 256]")
    n, m = len(source), len(target)
    if max(n, m) > 20_000:
        raise ValueError("Unexpected mitochondrial sequence length")
    width = 2 * band + 1
    traceback = bytearray((n + 1) * width)
    infinity = n + m + 1
    previous = {j: j for j in range(min(m, band) + 1)}
    for j in range(1, min(m, band) + 1):
        traceback[j + band] = 4
    for i in range(1, n + 1):
        current: dict[int, int] = {}
        for j in range(max(0, i - band), min(m, i + band) + 1):
            diagonal = (
                previous.get(j - 1, infinity)
                + (
                    source[i - 1] != target[j - 1]
                    or "N" in (source[i - 1], target[j - 1])
                )
                if j
                else infinity
            )
            deletion = previous.get(j, infinity) + 1
            insertion = current.get(j - 1, infinity) + 1
            best = min(diagonal, deletion, insertion)
            current[j] = best
            traceback[i * width + j - i + band] = (
                (1 if diagonal == best else 0)
                | (2 if deletion == best else 0)
                | (4 if insertion == best else 0)
            )
        previous = current
    score = previous.get(m, infinity)
    # Leaving this band needs at least band+1 unit-cost indels. Thus every
    # globally optimal path is inside the band when this inequality holds.
    if score > band:
        raise ValueError("Edit distance exceeds the globally complete alignment band")

    source_candidates = [-1] * (n + 1)
    target_candidates = [-1] * (m + 1)
    source_candidates[0] = target_candidates[0] = 0

    def candidate(values: list[int], position: int, match: int) -> None:
        if values[position] == -1:
            values[position] = match
        elif values[position] != match:
            values[position] = 0

    visited = bytearray(len(traceback))
    pending = deque([(n, m)])
    while pending:
        i, j = pending.popleft()
        cell = i * width + j - i + band
        if visited[cell]:
            continue
        visited[cell] = 1
        directions = traceback[cell]
        if directions & 1:
            candidate(source_candidates, i, j)
            candidate(target_candidates, j, i)
            pending.append((i - 1, j - 1))
        if directions & 2:
            candidate(source_candidates, i, 0)
            pending.append((i - 1, j))
        if directions & 4:
            candidate(target_candidates, j, 0)
            pending.append((i, j - 1))

    source_map = [0] * (n + 1)
    target_map = [0] * (m + 1)
    for i, j in enumerate(source_candidates[1:], 1):
        if (
            j > 0
            and target_candidates[j] == i
            and source[i - 1] in "ACGT"
            and target[j - 1] in "ACGT"
        ):
            source_map[i] = j
            target_map[j] = i

    aligned_source: list[str] = []
    aligned_target: list[str] = []
    i, j = n, m
    while i or j:
        directions = traceback[i * width + j - i + band]
        if directions & 1:
            aligned_source.append(source[i - 1])
            aligned_target.append(target[j - 1])
            i, j = i - 1, j - 1
        elif directions & 2:
            aligned_source.append(source[i - 1])
            aligned_target.append("-")
            i -= 1
        elif directions & 4:
            aligned_source.append("-")
            aligned_target.append(target[j - 1])
            j -= 1
        else:
            raise ValueError("Incomplete global-alignment traceback")
    return MitochondrialBridge(
        score,
        tuple(
            (position + target_rotation - 1) % m + 1 if position else 0
            for position in source_map
        ),
        tuple(
            [0]
            + [
                target_map[(position - target_rotation - 1) % m + 1]
                for position in range(1, m + 1)
            ]
        ),
        "".join(reversed(aligned_source)),
        "".join(reversed(aligned_target)),
        target_rotation,
    )


def mitochondrial_artifact(
    bridge: MitochondrialBridge, source: str, target: str, outdir: Path
) -> dict[str, object]:
    alignment = (
        f">rCRS_NC_012920.1\n{bridge.aligned_source}\n"
        f">CHM13v2.0_chrM\n{bridge.aligned_target}\n"
    ).encode()
    checksum = hashlib.sha256(alignment).hexdigest()
    filename = f"mt-alignment-v1-{checksum[:16]}.fa.gz"
    with (
        (outdir / filename).open("wb") as raw,
        gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as compressed,
    ):
        compressed.write(alignment)
    return {
        "algorithm": "unit-cost global Levenshtein, complete optimal-path graph",
        "algorithm_version": 1,
        "unknown_bases": "N always costs one substitution; N pairs never map",
        "target_rotation_offset0": bridge.target_rotation,
        "origin_policy": "unique exact first-40bp rCRS anchor; global alignment after origin normalization",
        "band": 128,
        "band_completeness_proof": "optimal_distance <= band",
        "alignment_tie_order": ["diagonal", "source_only", "target_only"],
        "mapping": "unique reciprocal pairs across ALL optimal alignments",
        "edit_distance": bridge.edit_distance,
        "rCRS_length": len(source),
        "rCRS_md5": hashlib.md5(source.encode()).hexdigest(),
        "rCRS_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "CHM13_length": len(target),
        "CHM13_md5": hashlib.md5(target.encode()).hexdigest(),
        "CHM13_sha256": hashlib.sha256(target.encode()).hexdigest(),
        "rCRS_no_call_positions": sum(pos == 0 for pos in bridge.source_to_target[1:]),
        "CHM13_no_call_positions": sum(pos == 0 for pos in bridge.target_to_source[1:]),
        "alignment_file": filename,
        "alignment_sha256": file_sha256(outdir / filename),
        "alignment_uncompressed_sha256": checksum,
    }
