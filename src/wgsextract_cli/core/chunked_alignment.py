"""Realign a coordinate-sorted, indexed BAM/CRAM of long reads in chunks
of its input (#431).

A whole-genome ONT realignment sorts more than its output in temporary
files and runs for days, so one pass needs about three times the output's
disk space and an interrupted run starts over. Here each chunk (a run of
genome regions of the input, about ``chunk_size`` bytes of it) is aligned
and sorted on its own, then split into the output's contig groups. Once
every chunk is done, each group is sorted across the chunks and appended to
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
import struct
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.core import alignment_splice
from wgsextract_cli.core.atomic_output import atomic_write
from wgsextract_cli.core.reference_assets import header_sequences
from wgsextract_cli.core.remote_input import copy_range, is_url
from wgsextract_cli.core.samtools_commands import alignment_index
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    run_command,
    run_pipeline,
)

SCHEMA = "wgsextract.chunked-alignment/3"
DEFAULT_CHUNK_SIZE = "4G"
# The output's contigs are split into about this many groups (and one of
# the unmapped reads): the largest, sorted beside the output, bounds the
# extra space assembly needs.
_OUTPUT_GROUPS = 24
# A BAM index's 16 kb windows are read in steps of this many bases.
_SEGMENT_BASES = 1 << 20
_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(text: str) -> int:
    """Bytes of a size such as 4G, 500M or 1048576 (0 turns chunking off)."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?)i?B?\s*", text, re.I)
    if not match:
        raise UsageError(f"Invalid size {text!r}: use a number with K, M, G or T.")
    return int(float(match.group(1)) * _UNITS[match.group(2).upper()])


@dataclass(frozen=True)
class Region:
    """A stretch of a contig (1-based, inclusive), or ``*``, the unplaced
    unmapped reads."""

    contig: str
    start: int = 1
    end: int = 0

    def __str__(self) -> str:
        return "*" if self.contig == "*" else f"{self.contig}:{self.start}-{self.end}"

    def bed(self) -> str:
        return f"{self.contig}\t{self.start - 1}\t{self.end}\n"


Chunk = tuple[Region, ...]
# samtools filter expression for the unplaced reads.
_UNPLACED = 'rname=="*"'


def _idxstats_unplaced(path: str, threads: str) -> int:
    """The alignment's unplaced reads, as samtools idxstats counts them."""
    lines = run_command(
        ["samtools", "idxstats", "-@", threads, path], capture_output=True
    ).stdout.splitlines()
    rows = [line.split("\t") for line in lines]
    return sum(int(row[3]) for row in rows if len(row) == 4 and row[0] == "*")


def chunk_filter(chunk: Chunk) -> list[str]:
    """samtools view options dropping the reads that start before the chunk:
    only its first region can start inside a contig."""
    first = chunk[0]
    if first.start <= 1:
        return []
    return ["-e", f'rname!="{first.contig}" || pos>={first.start}']


def describe(chunk: Chunk) -> str:
    shown = [str(region) for region in chunk]
    return ", ".join(shown[:3]) + (
        f" and {len(shown) - 3} more" if len(shown) > 3 else ""
    )


Segments = dict[str, list[tuple[int, int]]]


def _crai_rows(index: str) -> list[list[int]]:
    with open(index, "rb") as handle:
        data = handle.read()
    text = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    lines = [line.split() for line in text.decode().splitlines()]
    return [[int(field) for field in line[:6]] for line in lines if len(line) >= 6]


def crai_unplaced(index: str) -> tuple[int, int] | None:
    """Where a coordinate-sorted CRAM's records start (its first container)
    and the first container holding unplaced reads, from its index; None
    when it has none. Every container from there to the end holds only
    unplaced reads, but the first may also hold the last contig's."""
    rows = _crai_rows(index)
    unplaced = [row[3] for row in rows if row[0] < 0]
    if not unplaced:
        return None
    return min(row[3] for row in rows), min(unplaced)


def _crai_segments(index: str, names: Sequence[str]) -> tuple[Segments, int] | None:
    """Each contig's (position, bytes) slices, and the unmapped reads' bytes,
    from a CRAM index (gzipped or not): one line per slice and reference,
    in the file's order, with the reference ID, start, span, container
    offset, slice offset and slice size. None when the slices are out of
    coordinate order, whatever the header says: reading such a CRAM by
    region misses reads."""
    rows = _crai_rows(index)
    # A slice holding several references has a row for each, each with the
    # whole slice's size.
    shared: dict[tuple[int, int], int] = {}
    for row in rows:
        shared[row[3], row[4]] = shared.get((row[3], row[4]), 0) + 1
    segments: Segments = {}
    unmapped = 0
    last = (-1, 0)
    for reference, start, _, container, slice_, size in rows:
        share = size // shared[container, slice_]
        if reference < 0:
            unmapped += share
            last = (len(names), 0)
            continue
        if (reference, start) < last:
            return None
        last = (reference, start)
        if reference < len(names):
            segments.setdefault(names[reference], []).append((max(1, start), share))
    return segments, unmapped


def _bai_segments(index: str, names: Sequence[str], size: int) -> tuple[Segments, int]:
    """Each contig's bytes per _SEGMENT_BASES, and the unplaced reads' bytes,
    from a BAI's linear index (the file offset of each 16 kb window) and its
    per-contig offsets and read counts (pseudo-bin 37450). The offsets are of
    compressed blocks: a contig within one block may count 0 bytes."""
    with open(index, "rb") as handle:
        data = handle.read()
    if data[:4] != b"BAI\x01":
        raise WGSExtractError(f"{index} is not a BAI index.")
    (count,) = struct.unpack_from("<i", data, 4)
    offset = 8
    segments: Segments = {}
    end_of_reads = 0
    step = _SEGMENT_BASES >> 14
    for reference in range(count):
        (bins,) = struct.unpack_from("<i", data, offset)
        offset += 4
        span = None
        for _ in range(bins):
            number, chunks = struct.unpack_from("<Ii", data, offset)
            offset += 8
            if number == 37450:
                begin, end, mapped, unmapped = struct.unpack_from("<QQQQ", data, offset)
                if mapped + unmapped:
                    span = (begin >> 16, end >> 16)
            offset += 16 * chunks
        (windows,) = struct.unpack_from("<i", data, offset)
        offset += 4
        starts = struct.unpack_from(f"<{windows}Q", data, offset)
        offset += 8 * windows
        if span is None or reference >= len(names):
            continue
        begin, end = span
        end_of_reads = max(end_of_reads, end)
        marks = [begin]
        for window in range(step, windows, step):
            marks.append(min(end, max(marks[-1], starts[window] >> 16)))
        marks.append(end)
        segments[names[reference]] = [
            (1 + number * _SEGMENT_BASES, after - before)
            for number, (before, after) in enumerate(
                zip(marks, marks[1:], strict=False)
            )
        ]
    tail = size - len(alignment_splice.BGZF_EOF) - end_of_reads
    return segments, max(0, tail)


def _idxstats_segments(path: str, file_bytes: int) -> tuple[Segments, int]:
    """Each contig's reads spread evenly over its length, weighted by the
    file's size per record, from a BAM index's counts (samtools idxstats):
    for a CSI, which has no linear index."""
    lines = run_command(
        ["samtools", "idxstats", path], capture_output=True
    ).stdout.splitlines()
    counts = [line.split("\t") for line in lines if line.count("\t") == 3]
    records = sum(int(mapped) + int(unmapped) for _, _, mapped, unmapped in counts)
    per_record = file_bytes / max(1, records)
    segments: Segments = {}
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


@dataclass
class ChunkPlan:
    """An input's chunks, each one's estimated bytes of the input, and each
    contig's (``*``: the unplaced reads')."""

    chunks: list[Chunk]
    sizes: list[int]
    contigs: dict[str, int]


def plan_chunks(
    path: str,
    header: str,
    chunk_bytes: int,
    *,
    index: str | None = None,
    input_bytes: int | None = None,
) -> ChunkPlan | None:
    """The input's chunks of about ``chunk_bytes`` each, in its order; None
    when it can't be read by region (not coordinate-sorted or not indexed).
    The unplaced reads are a chunk of their own."""
    index = index or alignment_index(path)
    if index is None or "SO:coordinate" not in (header.splitlines() or [""])[0]:
        return None
    sequences = header_sequences(header)
    names = [sequence["SN"] for sequence in sequences]
    lengths = {sequence["SN"]: int(sequence["LN"]) for sequence in sequences}
    if index.endswith(".crai"):
        planned = _crai_segments(index, names)
        if planned is None:
            logging.warning(
                "%s says it is coordinate-sorted, but its index lists slices "
                "out of order.",
                path,
            )
            return None
        segments, unmapped = planned
    elif index.endswith(".bai"):
        segments, unmapped = _bai_segments(
            index, names, input_bytes or os.path.getsize(path)
        )
    else:
        segments, unmapped = _idxstats_segments(
            f"{path}##idx##{index}", input_bytes or os.path.getsize(path)
        )
    plan = ChunkPlan([], [], {})
    current: list[Region] = []
    size = 0

    def close(region: Region | None = None) -> None:
        nonlocal current, size
        if region is not None:
            current.append(region)
        if current:
            plan.chunks.append(tuple(current))
            plan.sizes.append(size)
        current, size = [], 0

    for name in names:
        if name not in segments:
            continue  # No reads: nothing to align.
        plan.contigs[name] = sum(bytes_ for _, bytes_ in segments[name])
        if size >= chunk_bytes:
            close()
        start = 1
        for position, bytes_ in sorted(segments[name]):
            if size >= chunk_bytes and position > start:
                close(Region(name, start, position - 1))
                start = position
            size += bytes_
        current.append(Region(name, start, lengths[name]))
    close()
    if unmapped:
        plan.contigs["*"] = unmapped
        size = unmapped
        close(Region("*"))
    return plan


def contigs(reference: str) -> list[tuple[str, int]]:
    """The reference's contigs and their lengths: from its .fai, or read
    from the FASTA (samtools dict) without writing beside it."""
    fai = Path(reference + ".fai")
    if fai.is_file():
        rows = [line.split("\t") for line in fai.read_text().splitlines()]
        return [(row[0], int(row[1])) for row in rows if len(row) > 1]
    found = []
    text = run_command(["samtools", "dict", reference], capture_output=True).stdout
    for sequence in header_sequences(text):
        found.append((sequence["SN"], int(sequence["LN"])))
    return found


def output_groups(contigs: Sequence[tuple[str, int]]) -> list[Chunk]:
    """The output's contigs (its reference's, in order) in about
    _OUTPUT_GROUPS consecutive groups of similar length, then the unmapped
    reads as a group of their own."""
    target = sum(length for _, length in contigs) / _OUTPUT_GROUPS
    groups: list[list[Region]] = [[]]
    size = 0
    for name, length in contigs:
        if size >= target:
            groups.append([])
            size = 0
        groups[-1].append(Region(name, 1, length))
        size += length
    return [tuple(group) for group in groups] + [(Region("*"),)]


def chunked_disk(
    output_bytes: int,
    spill_bytes: int,
    chunk_spill_bytes: int,
    plan: ChunkPlan,
    reference: Sequence[tuple[str, int]],
) -> tuple[int, int]:
    """Bytes a chunked alignment needs at its peak at its output's directory
    and for its spills (``spill_bytes``: a single pass's; ``chunk_spill_bytes``:
    its largest chunk's). Beside the output's groups (all of it, eventually)
    are the aligner's index (minimap2's is about 2.4 bytes per reference
    base) while aligning, and its largest group, sorted beside the output,
    after. The spills are one chunk's, or one group's while it is sorted.

    A group's share of the output is its contigs' share of the input when
    the input names the reference's contigs. Otherwise (``1`` realigned to
    ``chr1``, say) it is its share of the reference's length, and at least
    twice the average group's."""
    total = max(1, sum(plan.contigs.values()))
    groups = output_groups(reference)
    names = {name for name, _ in reference} | {"*"}
    if set(plan.contigs) <= names:
        share = max(
            sum(plan.contigs.get(region.contig, 0) for region in group) / total
            for group in groups
        )
    else:
        length = max(1, sum(size for _, size in reference))
        share = max(
            2 / _OUTPUT_GROUPS,
            plan.contigs.get("*", 0) / total,
            *(
                sum(r.end for r in group if r.contig != "*") / length
                for group in groups
            ),
        )
    bases = sum(length for _, length in reference)
    return (
        math.ceil(max(output_bytes + 2.4 * bases, output_bytes * (1 + share))),
        math.ceil(max(chunk_spill_bytes, spill_bytes * share)),
    )


@dataclass
class ChunkedAlignment:
    """A chunked long-read alignment of ``input`` into ``output``.

    ``mapper``: the aligner's argv up to its reference (minimap2 -ax PRESET
    -t N -Y -y -R RG); it reads the chunk's FASTQ from stdin. ``indexer``:
    the argv that builds the aligner's index (minimap2 -x PRESET -t N).
    ``reads``: the stage turning a chunk's records into the aligner's FASTQ.
    ``resume``: whether the work of an interrupted run with the same
    settings may be kept. The work directory, ``<output>.chunks``, is left
    with a finished state for the caller to remove once it has recorded the
    output: until then a rerun finds the output done.
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
        groups = output_groups(contigs(self.reference))
        state = self._state(groups)
        if not state.get("done"):
            if len(state["chunks"]) < len(self.chunks):
                self._preflight()
                index = self._index(state)
                for number, chunk in enumerate(self.chunks):
                    self._align_chunk(state, number, chunk, groups, index)
                Path(index).unlink(missing_ok=True)
            self._assemble(state, groups)
        if self._assembly().is_file():
            # The output's old index must not outlive it.
            for index in (".bai", ".crai", ".csi"):
                Path(self.output + index).unlink(missing_ok=True)
            Path(self.output).with_suffix(".bai").unlink(missing_ok=True)
            os.replace(self._assembly(), self.output)

    # --- state -----------------------------------------------------------

    def _engine(self) -> str:
        """The aligner's version, so that another build restarts the work."""
        try:
            done = subprocess.run(
                [self.mapper[0], "--version"],
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
            )
        except OSError:
            return ""
        return done.stdout.strip()

    def _key(self, groups: list[Chunk]) -> str:
        def settings(argv: list[str]) -> list[str]:
            # Threads don't change the alignment.
            return [a for i, a in enumerate(argv) if "-t" not in argv[i - 1 : i] + [a]]

        plan = {
            "input": self.input,
            "chunks": [[str(region) for region in chunk] for chunk in self.chunks],
            "groups": [[str(region) for region in group] for group in groups],
            "format": self.format,
            "reference": self.reference,
            "mapper": settings(self.mapper),
            "indexer": settings(self.indexer),
            "engine": self._engine(),
        }
        text = json.dumps(plan, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()

    def _state(self, groups: list[Chunk]) -> dict[str, Any]:
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

    def _cram_source(self) -> tuple[str, str] | None:
        """A CRAM input's path or URL and its index; None for a BAM."""
        source, _, index = self.input.partition("##idx##")
        if not source.lower().endswith(".cram"):
            return None
        return source, index or alignment_index(source) or ""

    def _stage_unplaced(self, name: str) -> Path | None:
        """A CRAM of the CRAM input's header and its containers from the
        first holding unplaced reads to its end (#655); None when its index
        lists none. htslib before 1.25 reads ``*`` from a CRAM by its index
        wrongly, missing whole containers of them; this needs no query. Its
        first container may also hold the last contig's reads: read it with
        ``rname=="*"``."""
        cram = self._cram_source()
        if cram is None:
            return None
        source, index = cram
        layout = crai_unplaced(index) if index.endswith(".crai") else None
        if layout is None:
            return None
        records, unplaced = layout
        path = self._temp(name)
        with open(path, "wb") as output:
            copy_range(source, 0, records, output)
            copy_range(source, unplaced, None, output)
        return path

    def _check_unplaced(self, staged: Path | None) -> None:
        """Fail unless the unplaced reads ``staged`` holds (from the index)
        are all a local CRAM input's (samtools idxstats): also when its index
        lists none, and no chunk would read them."""
        cram = self._cram_source()
        if cram is None or is_url(cram[0]):
            return
        found = 0
        if staged is not None:
            found = int(
                run_command(
                    [
                        "samtools",
                        "view",
                        "-c",
                        *self.decode,
                        "-e",
                        _UNPLACED,
                        str(staged),
                    ],
                    capture_output=True,
                ).stdout  # fmt: skip
            )
        expected = _idxstats_unplaced(self.input, self.threads)
        if found != expected:
            raise WGSExtractError(
                f"{cram[0]} has {expected} unplaced unmapped reads "
                f"(samtools idxstats), but its index finds {found}."
            )

    def _preflight(self) -> None:
        """Check a local CRAM input's unplaced reads before hours of
        alignment, rather than at their chunk, aligned last."""
        cram = self._cram_source()
        if cram is None or is_url(cram[0]):
            return
        staged = self._stage_unplaced("preflight.unplaced.cram")
        try:
            self._check_unplaced(staged)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    def _unplaced_cram(self, chunk: Chunk, number: int) -> Path | None:
        """The unplaced chunk's reads of a CRAM input (_stage_unplaced),
        checked again (_check_unplaced)."""
        if chunk != (Region("*"),):
            return None
        path = self._stage_unplaced(f"chunk-{number:05d}.unplaced.cram")
        if path is not None:
            self._check_unplaced(path)
        return path

    def _view(
        self, options: list[str], path: str, regions: Chunk, name: str
    ) -> list[str]:
        """samtools view of ``regions`` of ``path``: through a BED file in the
        work directory (command lines have limits; thousands of contigs
        overflow Windows'), or ``*``."""
        if regions == (Region("*"),):
            return ["samtools", "view", *options, path, "*"]
        bed = self.workdir / f"{name}.bed"
        if not bed.is_file():
            atomic_write(bed, "".join(r.bed() for r in regions).encode("utf-8"))
        return ["samtools", "view", *options, "--region-file", str(bed), path]

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
        groups: list[Chunk],
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
        view = ["-u", "-F", "0x900", *chunk_filter(chunk), *self.decode]
        unplaced = self._unplaced_cram(chunk, number)
        run_pipeline(
            [
                self._view(view, self.input, chunk, f"chunk-{number:05d}")
                if unplaced is None
                else ["samtools", "view", *view, "-e", _UNPLACED, str(unplaced)],
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
                    # CSI: a BAI can't index contigs longer than 512 Mb.
                    f"{sorted_chunk}##idx##{sorted_chunk}.csi",
                    "-",
                ],
            ],  # fmt: skip
            description=f"Alignment of chunk {number + 1} of {total}",
            heartbeat=True,
        )
        sizes = []
        for group, regions in enumerate(groups):
            path = self._group_file(group, number)
            options = ["--no-PG", "-@", self.threads, *self._output_options(),
                       "-o", str(path)]  # fmt: skip
            run_command(
                self._view(options, str(sorted_chunk), regions, f"group-{group:03d}")
            )
            sizes.append(path.stat().st_size)
        sorted_chunk.unlink()
        Path(f"{sorted_chunk}.csi").unlink(missing_ok=True)
        if unplaced is not None:
            unplaced.unlink()
        state["chunks"][str(number)] = sizes
        self._save(state)

    def _assemble(self, state: dict[str, Any], groups: list[Chunk]) -> None:
        """Merge each group across the chunks and append it to the
        assembly, deleting its pieces as it goes."""
        assembly = self._assembly()
        for group in range(state["assembled"], len(groups)):
            piece = self._piece(group)
            if group not in state["merged"]:
                logging.info(
                    "Merging output group %d of %d ...", group + 1, len(groups)
                )
                files = self.workdir / f"group-{group:03d}.files"
                atomic_write(
                    files,
                    "".join(
                        f"{self._group_file(group, n)}\n"
                        for n in range(len(self.chunks))
                    ).encode("utf-8"),
                )
                # Not samtools merge: it moves each record's RG tag to its
                # end. sort is stable, so records of the same position keep
                # the chunks' order, which is the input's, as in one pass.
                sort_prefix = self._temp(f"group-{group:03d}.sort")
                run_pipeline(
                    [
                        ["samtools", "cat", "--no-PG", "-b", str(files), "-o", "-"],
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
