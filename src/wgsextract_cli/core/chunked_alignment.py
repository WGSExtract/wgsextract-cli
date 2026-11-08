"""Realign a coordinate-sorted, indexed BAM/CRAM of long reads in chunks
of its input (#431).

A whole-genome ONT realignment sorts more than its output in temporary
files and runs for days, so one pass needs about three times the output's
disk space and an interrupted run starts over. Here each chunk (a run of
genome regions of the input, about ``chunk_size`` bytes of it) is aligned
and sorted on its own, then split into the output's contig groups. Once
every chunk is done, each group is merged across the chunks and appended to
the output (core.alignment_splice), and its pieces are deleted. Temporary
files therefore stay chunk-sized and the output is written once, beside the
groups it replaces. ``<output>.chunks/state.json`` records each finished
chunk and appended group, so a rerun resumes at the first unfinished one.

Each primary read is aligned once: a chunk reads the primary records that
start in its regions (``samtools view`` returns the reads overlapping a
region, so those starting before it are filtered out). Records of the same
position keep the input's order, so the output's records are the same as
those of a single pass; only its header lacks the sort's @PG line.
"""

from __future__ import annotations

import glob
import gzip
import hashlib
import json
import logging
import math
import os
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core import alignment_splice
from wgsextract_cli.core.atomic_output import atomic_write
from wgsextract_cli.core.reference_assets import header_sequences
from wgsextract_cli.core.samtools_commands import alignment_index
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import (
    UsageError,
    run_command,
    run_pipeline,
)

SCHEMA = "wgsextract.chunked-alignment/1"
DEFAULT_CHUNK_SIZE = "4G"
# The output's contigs are split into about this many groups: the largest
# group, merged beside the output, bounds the extra space assembly needs.
_OUTPUT_GROUPS = 24
# An index without positions (idxstats) is split evenly within each contig.
_SEGMENT_BASES = 1_000_000
_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(text: str) -> int:
    """Bytes of a size such as 4G, 500M or 1048576 (0 turns chunking off)."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?)i?B?\s*", text, re.I)
    if not match:
        raise UsageError(f"Invalid size {text!r}: use a number with K, M, G or T.")
    return int(float(match.group(1)) * _UNITS[match.group(2).upper()])


@dataclass(frozen=True)
class Region:
    """A stretch of a contig (1-based, inclusive; ``end`` None: to its end),
    or ``*``, the unplaced unmapped reads."""

    contig: str
    start: int = 1
    end: int | None = None

    def spec(self) -> str:
        if self.contig == "*":
            return "*"
        # Braces keep names such as HLA-A*01:01:01:01 whole.
        name = "{" + self.contig + "}"
        if self.start == 1 and self.end is None:
            return name
        return f"{name}:{self.start}-{self.end or ''}"


Chunk = tuple[Region, ...]


def chunk_filter(chunk: Chunk) -> list[str]:
    """samtools view options dropping the reads that start before the chunk:
    only its first region can start inside a contig."""
    first = chunk[0]
    if first.start <= 1:
        return []
    return ["-e", f'rname!="{first.contig}" || pos>={first.start}']


def describe(chunk: Chunk) -> str:
    shown = [region.spec().replace("{", "").replace("}", "") for region in chunk]
    return ", ".join(shown[:3]) + (
        f" and {len(shown) - 3} more" if len(shown) > 3 else ""
    )


def _crai_segments(
    index: str, names: Sequence[str]
) -> tuple[dict[str, list[tuple[int, int]]], int]:
    """Each contig's (position, bytes) slices, and the unmapped reads' bytes,
    from a CRAM index: one line per slice with its reference ID, start,
    span, container offset, slice offset and size."""
    segments: dict[str, list[tuple[int, int]]] = {}
    unmapped = 0
    with gzip.open(index, "rt") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) < 6:
                continue
            reference, start, size = int(fields[0]), int(fields[1]), int(fields[5])
            if reference < 0:
                unmapped += size
            elif reference < len(names):
                segments.setdefault(names[reference], []).append((max(1, start), size))
    return segments, unmapped


def _idxstats_segments(path: str) -> tuple[dict[str, list[tuple[int, int]]], int]:
    """Each contig's reads spread evenly over its length, weighted by the
    file's size per record, from a BAM index's counts (samtools idxstats)."""
    lines = run_command(
        ["samtools", "idxstats", path], capture_output=True
    ).stdout.splitlines()
    counts = [line.split("\t") for line in lines if line.count("\t") == 3]
    records = sum(int(mapped) + int(unmapped) for _, _, mapped, unmapped in counts)
    per_record = os.path.getsize(path) / max(1, records)
    segments: dict[str, list[tuple[int, int]]] = {}
    unplaced = 0
    for name, length, mapped, unmapped in counts:
        size = (int(mapped) + int(unmapped)) * per_record
        if name == "*":
            unplaced = round(size)
        elif size:
            pieces = max(1, math.ceil(int(length) / _SEGMENT_BASES))
            segments[name] = [
                (1 + piece * _SEGMENT_BASES, round(size / pieces))
                for piece in range(pieces)
            ]
    return segments, unplaced


def plan_chunks(path: str, header: str, chunk_bytes: int) -> list[Chunk] | None:
    """The input's chunks of about ``chunk_bytes`` each, in its order; None
    when it can't be read by region (not coordinate-sorted or not indexed)."""
    index = alignment_index(path)
    if index is None or "SO:coordinate" not in (header.splitlines() or [""])[0]:
        return None
    names = [sequence["SN"] for sequence in header_sequences(header)]
    if index.endswith(".crai"):
        segments, unmapped = _crai_segments(index, names)
    else:
        segments, unmapped = _idxstats_segments(path)
    chunks: list[Chunk] = []
    current: list[Region] = []
    size = 0

    def close(region: Region | None = None) -> None:
        nonlocal current, size
        if region is not None:
            current.append(region)
        if current:
            chunks.append(tuple(current))
        current, size = [], 0

    for name in names:
        if name not in segments:
            continue  # No reads: nothing to align.
        if size >= chunk_bytes:
            close()
        start = 1
        for position, bytes_ in sorted(segments[name]):
            if size >= chunk_bytes and position > start:
                close(Region(name, start, position - 1))
                start = position
            size += bytes_
        current.append(Region(name, start))
    if unmapped:
        if size >= chunk_bytes:
            close()
        current.append(Region("*"))
    close()
    return chunks


def _contigs(reference: str) -> list[tuple[str, int]]:
    """The reference's contigs and their lengths, from its .fai."""
    fai = Path(reference + ".fai")
    if not fai.is_file():
        run_command(["samtools", "faidx", reference])
    return [
        (fields[0], int(fields[1]))
        for fields in (line.split("\t") for line in fai.read_text().splitlines())
        if len(fields) > 1
    ]


def output_groups(reference: str) -> list[list[str]]:
    """The output's contigs (its reference's, in order) in about
    _OUTPUT_GROUPS consecutive groups of similar length, as region specs;
    the last group also takes the unmapped reads."""
    contigs = _contigs(reference)
    target = sum(length for _, length in contigs) / _OUTPUT_GROUPS
    groups: list[list[str]] = [[]]
    size = 0
    for name, length in contigs:
        if size >= target:
            groups.append([])
            size = 0
        groups[-1].append(Region(name).spec())
        size += length
    groups[-1].append("*")
    return groups


@dataclass
class ChunkedAlignment:
    """A chunked long-read alignment of ``input`` into ``output``.

    ``mapper``: the aligner's argv up to its reference (minimap2 -ax PRESET
    -t N -Y -y -R RG); it reads the chunk's FASTQ from stdin. ``indexer``:
    the argv that builds the aligner's index (minimap2 -x PRESET -t N).
    ``reads``: the stage turning a chunk's records into the aligner's FASTQ.
    ``resume``: whether the work of an interrupted run with the same
    settings may be kept.
    """

    input: str
    output: str
    chunks: list[Chunk]
    reference: str
    format: str
    threads: str
    memory: str
    mapper: list[str]
    indexer: list[str]
    reads: list[str]
    decode: list[str]
    resume: bool = False

    @property
    def workdir(self) -> Path:
        return Path(self.output + ".chunks")

    def run(self) -> None:
        """Write the output (without its index), resuming the work of an
        interrupted run with the same plan when ``resume`` allows it."""
        groups = output_groups(self.reference)
        state = self._state(groups)
        if not state.get("done"):
            if len(state["chunks"]) < len(self.chunks):
                index = self._index(state)
                for number, chunk in enumerate(self.chunks):
                    self._align_chunk(state, number, chunk, groups, index)
                Path(index).unlink(missing_ok=True)
            self._assemble(state, groups)
        if self._assembly().is_file():
            os.replace(self._assembly(), self.output)
        shutil.rmtree(self.workdir, ignore_errors=True)

    # --- state -----------------------------------------------------------

    def _key(self, groups: list[list[str]]) -> str:
        plan = {
            "input": self.input,
            "chunks": [[region.spec() for region in chunk] for chunk in self.chunks],
            "groups": groups,
            "format": self.format,
            "reference": self.reference,
        }
        text = json.dumps(plan, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()

    def _state(self, groups: list[list[str]]) -> dict[str, Any]:
        key = self._key(groups)
        path = self.workdir / "state.json"
        state: dict[str, Any] | None = None
        if self.resume:
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = None
        if state and state.get("schema") == SCHEMA and state.get("key") == key:
            if self._intact(state, len(groups)):
                done = len(state["chunks"])
                logging.info(
                    "Resuming the chunked alignment: %d of %d chunks and %d of %d "
                    "groups done.",
                    done,
                    len(self.chunks),
                    state["assembled"],
                    len(groups),
                )
                return state
            logging.warning(
                "Restarting the chunked alignment: files an interrupted run kept "
                "in %s are missing or changed.",
                self.workdir,
            )
        elif self.workdir.exists():
            logging.info("Removing %s, left by an earlier run.", self.workdir)
        shutil.rmtree(self.workdir, ignore_errors=True)
        self.workdir.mkdir(parents=True)
        state = {
            "schema": SCHEMA,
            "key": key,
            "index": False,
            "chunks": {},
            "merged": [],
            "assembled": 0,
            "bytes": 0,
            "done": False,
        }
        self._save(state)
        return state

    def _intact(self, state: dict[str, Any], groups: int) -> bool:
        """Whether every file the state counts on is there as recorded."""
        merged = set(state["merged"])
        for number, sizes in state["chunks"].items():
            for group, size in enumerate(sizes):
                if group in merged:
                    continue
                path = self._group_file(group, int(number))
                if not path.is_file() or path.stat().st_size != size:
                    return False
        if merged and len(state["chunks"]) != len(self.chunks):
            return False
        for group in merged:
            if group >= state["assembled"] and not self._piece(group).is_file():
                return False
        assembly = self._assembly()
        if state.get("done"):
            # Finished; it may already have been moved to the output.
            return assembly.is_file() or Path(self.output).is_file()
        return not state["bytes"] or (
            assembly.is_file() and assembly.stat().st_size >= state["bytes"]
        )

    def _save(self, state: dict[str, Any]) -> None:
        text = json.dumps(state, indent=1, sort_keys=True) + "\n"
        atomic_write(self.workdir / "state.json", text.encode("utf-8"))

    def _extension(self) -> str:
        return ".cram" if self.format == "CRAM" else ".bam"

    def _group_file(self, group: int, chunk: int) -> Path:
        return (
            self.workdir
            / f"group-{group:03d}"
            / f"chunk-{chunk:05d}{self._extension()}"
        )

    def _piece(self, group: int) -> Path:
        return self.workdir / f"piece-{group:03d}{self._extension()}"

    def _assembly(self) -> Path:
        return self.workdir / f"assembly{self._extension()}"

    def _temp(self, name: str) -> Path:
        """A temporary file of this alignment's in the temporary directory
        (or its work directory), cleared of an interrupted run's spills."""
        directory = configured_temp_directory()
        if not directory:
            path = self.workdir / name
        else:
            tag = hashlib.sha256(str(self.workdir.resolve()).encode()).hexdigest()
            path = Path(directory) / f"{self.workdir.name}-{tag[:8]}-{name}"
        for stale in path.parent.glob(glob.escape(path.name) + "*"):
            stale.unlink(missing_ok=True)
        return path

    def _output_options(self) -> list[str]:
        if self.format == "CRAM":
            return ["-O", "CRAM", "--reference", self.reference]
        return ["-O", "BAM"]

    # --- steps -----------------------------------------------------------

    def _index(self, state: dict[str, Any]) -> str:
        """The aligner's index of the reference, built once for every chunk."""
        index = str(self.workdir / "reference.mmi")
        if state["index"] and Path(index).is_file():
            return index
        logging.info("Indexing %s for the chunks' alignments ...", self.reference)
        staged = index + ".partial"
        run_command([*self.indexer, "-d", staged, self.reference])
        os.replace(staged, index)
        state["index"] = True
        self._save(state)
        return index

    def _align_chunk(
        self,
        state: dict[str, Any],
        number: int,
        chunk: Chunk,
        groups: list[list[str]],
        index: str,
    ) -> None:
        """Align and sort one chunk, then split it into the output's groups."""
        if str(number) in state["chunks"]:
            return
        total = len(self.chunks)
        logging.info("Aligning chunk %d of %d: %s", number + 1, total, describe(chunk))
        for group in range(len(groups)):
            self._group_file(group, number).parent.mkdir(exist_ok=True)
            self._group_file(group, number).unlink(missing_ok=True)
        sorted_chunk = self._temp(f"chunk-{number:05d}.bam")
        sort_prefix = self._temp(f"chunk-{number:05d}.sort")
        view = ["samtools", "view", "-u", "-F", "0x900", *chunk_filter(chunk)]
        run_pipeline(
            [
                [*view, *self.decode, self.input, *(r.spec() for r in chunk)],
                self.reads,
                [*self.mapper, index, "-"],
                [
                    "samtools",
                    "sort",
                    "--no-PG",
                    "-@",
                    self.threads,
                    "-m",
                    self.memory,
                    "-T",
                    str(sort_prefix),
                    "-l",
                    "1",
                    "-O",
                    "BAM",
                    "--write-index",
                    "-o",
                    f"{sorted_chunk}##idx##{sorted_chunk}.bai",
                    "-",
                ],
            ],  # fmt: skip
            description=f"Alignment of chunk {number + 1} of {total}",
            heartbeat=True,
        )
        sizes = []
        for group, regions in enumerate(groups):
            path = self._group_file(group, number)
            run_command(
                ["samtools", "view", "--no-PG", "-@", self.threads,
                 *self._output_options(), "-o", str(path), str(sorted_chunk),
                 *regions]
            )  # fmt: skip
            sizes.append(path.stat().st_size)
        sorted_chunk.unlink()
        Path(f"{sorted_chunk}.bai").unlink(missing_ok=True)
        state["chunks"][str(number)] = sizes
        self._save(state)

    def _assemble(self, state: dict[str, Any], groups: list[list[str]]) -> None:
        """Merge each group across the chunks and append it to the
        assembly, deleting its pieces as it goes."""
        assembly = self._assembly()
        for group in range(state["assembled"], len(groups)):
            piece = self._piece(group)
            if group not in state["merged"]:
                logging.info(
                    "Merging output group %d of %d ...", group + 1, len(groups)
                )
                files = [
                    str(self._group_file(group, n)) for n in range(len(self.chunks))
                ]
                # Not samtools merge: it moves each record's RG tag to its
                # end. sort is stable, so records of the same position keep
                # the chunks' order, which is the input's, as in one pass.
                sort_prefix = self._temp(f"group-{group:03d}.sort")
                run_pipeline(
                    [
                        ["samtools", "cat", "--no-PG", "-o", "-", *files],
                        [
                            "samtools",
                            "sort",
                            "--no-PG",
                            "-@",
                            self.threads,
                            "-m",
                            self.memory,
                            "-T",
                            str(sort_prefix),
                            *self._output_options(),
                            "-o",
                            str(piece),
                            "-",
                        ],
                    ],  # fmt: skip
                    description=f"Merge of output group {group + 1}",
                )
                state["merged"].append(group)
                self._save(state)
                shutil.rmtree(self._group_file(group, 0).parent, ignore_errors=True)
            state["bytes"] = alignment_splice.append(assembly, piece, state["bytes"])
            state["assembled"] = group + 1
            self._save(state)
            piece.unlink()
        alignment_splice.finish(assembly, state["bytes"])
        state["done"] = True
        self._save(state)


def chunked_disk(
    output_bytes: int, spill_bytes: int, chunk_spill_bytes: int, reference: str | None
) -> tuple[int, int]:
    """Bytes a chunked alignment needs at its peak at its output's directory
    and for its spills (``spill_bytes``: a single pass's). Beside the output's
    groups (all of it, eventually) are the aligner's index (minimap2's is
    about 2.4 bytes per reference base) while aligning, and the group being
    assembled (perhaps twice the average) after. The spills are one chunk's,
    or one group's while it is sorted."""
    bases = 0
    if reference and Path(reference).is_file():
        bases = sum(length for _, length in _contigs(reference))
    group = 2 / _OUTPUT_GROUPS
    return (
        math.ceil(max(output_bytes + 2.4 * bases, output_bytes * (1 + group))),
        math.ceil(max(chunk_spill_bytes, spill_bytes * group)),
    )
