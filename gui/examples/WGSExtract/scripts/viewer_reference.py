"""Resolve the viewer's reference locally using the CLI's sequence dictionary."""

import gzip
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_LOCUS_WIDTH = 100
_MIDDLE_PROBE_ATTEMPTS = 8


def _alignment_index_path(input_path: str) -> Path | None:
    lower = input_path.lower()
    candidates: tuple[str, ...]
    if lower.endswith(".bam"):
        candidates = (f"{input_path}.bai", f"{input_path[:-4]}.bai", f"{input_path}.csi")
    elif lower.endswith(".cram"):
        candidates = (f"{input_path}.crai", f"{input_path[:-5]}.crai")
    else:
        return None
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def _middle_bounds(length: int) -> tuple[int, int]:
    low = max(1, length // 4)
    high = min(
        max(low, (3 * length) // 4 - DEFAULT_LOCUS_WIDTH + 1),
        length - DEFAULT_LOCUS_WIDTH + 1,
    )
    return low, high


def _window_at(contig: str, start: int, length: int) -> str:
    if length <= DEFAULT_LOCUS_WIDTH:
        return f"{contig}:1-{length}"
    clamped = max(1, min(start, length - DEFAULT_LOCUS_WIDTH + 1))
    return f"{contig}:{clamped}-{clamped + DEFAULT_LOCUS_WIDTH - 1}"


def _first_mapped_read(
    tool: str, input_path: str, region: str
) -> tuple[str, int] | None:
    from wgsextract_cli.core.process_registry import terminate_trees
    from wgsextract_cli.core.utils import popen

    # For CRAM, no_ref=1 skips reference decoding so probing stays fast and
    # strictly offline (never triggering REF_PATH/EBI lookups).
    command = [tool, "view"]
    if input_path.lower().endswith(".cram"):
        command.extend(["--input-fmt-option", "no_ref=1"])
    command.extend(["-F", "0x904", input_path, region])
    proc = popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        line = proc.stdout.readline() if proc.stdout else ""
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.poll() is None:
            terminate_trees([proc])
        else:
            proc.wait(timeout=5)
    fields = line.split("\t")
    if len(fields) >= 4 and fields[2] != "*" and fields[3].isdigit():
        position = int(fields[3])
        if position >= 1:
            return fields[2], position
    return None


def _bam_first_mapped_contig(
    tool: str, input_path: str, lengths: dict[str, int]
) -> str | None:
    from wgsextract_cli.core.utils import run_command

    result = run_command([tool, "idxstats", input_path], capture_output=True)
    mapped: dict[str, int] = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) >= 3 and fields[2].isdigit():
            mapped[fields[0]] = int(fields[2])
    for contig in lengths:
        if mapped.get(contig, 0) > 0:
            return contig
    return None


def _crai_default_locus(crai_path: Path, lengths: dict[str, int]) -> str | None:
    contig_names = list(lengths)
    first_ref_id: int | None = None
    first_start: int | None = None
    middle_slices: list[tuple[int, int]] = []
    middle_low = 1
    middle_high = 1
    try:
        with gzip.open(crai_path, "rt", encoding="ascii") as handle:
            for line in handle:
                fields = line.split("\t")
                if len(fields) < 3:
                    continue
                ref_id = int(fields[0])
                alignment_start = int(fields[1])
                alignment_span = int(fields[2])
                if (
                    ref_id < 0
                    or ref_id >= len(contig_names)
                    or alignment_start < 1
                    or alignment_span < 1
                ):
                    continue
                if first_ref_id is None:
                    first_ref_id = ref_id
                    length = lengths[contig_names[ref_id]]
                    if length <= DEFAULT_LOCUS_WIDTH:
                        return f"{contig_names[ref_id]}:1-{length}"
                    middle_low, middle_high = _middle_bounds(length)
                elif ref_id != first_ref_id:
                    if ref_id > first_ref_id:
                        break
                    continue
                if first_start is None or alignment_start < first_start:
                    first_start = alignment_start
                slice_end = alignment_start + alignment_span - 1
                if (
                    slice_end >= middle_low
                    and alignment_start <= middle_high + DEFAULT_LOCUS_WIDTH - 1
                ):
                    middle_slices.append((alignment_start, slice_end))
    except (OSError, ValueError):
        return None
    if first_ref_id is None:
        contig = contig_names[0]
        length = lengths[contig]
        if length <= DEFAULT_LOCUS_WIDTH:
            return f"{contig}:1-{length}"
        low, high = _middle_bounds(length)
        return _window_at(contig, random.randint(low, high), length)
    contig = contig_names[first_ref_id]
    length = lengths[contig]
    if middle_slices:
        slice_start, slice_end = random.choice(middle_slices)
        pick_low = max(middle_low, slice_start)
        pick_high = min(middle_high, max(pick_low, slice_end))
        return _window_at(contig, random.randint(pick_low, pick_high), length)
    assert first_start is not None
    return _window_at(contig, first_start, length)


def _default_locus(
    lengths: dict[str, int], input_path: str, tool: str, is_alignment: bool
) -> str:
    first = next(iter(lengths))
    index_path = _alignment_index_path(input_path) if is_alignment else None
    if index_path is not None and input_path.lower().endswith(".cram"):
        crai_locus = _crai_default_locus(index_path, lengths)
        if crai_locus is not None:
            return crai_locus
    if index_path is not None and input_path.lower().endswith(".bam"):
        contig = _bam_first_mapped_contig(tool, input_path, lengths)
        if contig is not None:
            length = lengths[contig]
            if length <= DEFAULT_LOCUS_WIDTH:
                return f"{contig}:1-{length}"
            low, high = _middle_bounds(length)
            for _ in range(_MIDDLE_PROBE_ATTEMPTS):
                start = random.randint(low, high)
                end = start + DEFAULT_LOCUS_WIDTH - 1
                if _first_mapped_read(tool, input_path, f"{contig}:{start}-{end}"):
                    return f"{contig}:{start}-{end}"
            first_read = _first_mapped_read(tool, input_path, contig)
            if first_read is not None:
                return _window_at(contig, first_read[1], length)
    length = lengths[first]
    if length <= DEFAULT_LOCUS_WIDTH:
        return f"{first}:1-{length}"
    low, high = _middle_bounds(length)
    return _window_at(first, random.randint(low, high), length)


def resolve_reference(
    input_path: str, config_path: str, *, include_locus: bool = True
) -> dict[str, Any]:
    from wgsextract_cli.core.alignment.metadata import get_bam_header
    from wgsextract_cli.core.config import reload_settings, set_config_path
    from wgsextract_cli.core.dependencies import get_tool_path
    from wgsextract_cli.core.ref_library.assets import (
        header_sequences,
        read_fai_lengths,
        reference_dict_path,
        reference_fasta_candidates,
        reference_genomes_dir,
        sequence_signature,
    )
    from wgsextract_cli.core.ref_library.lookup import local_reference_lookup
    from wgsextract_cli.core.ref_library.models import sequence_lengths
    from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary, library_roots
    from wgsextract_cli.core.utils import run_command

    with local_reference_lookup():
        set_config_path(config_path)
        reload_settings()
        is_alignment = input_path.lower().endswith((".bam", ".cram"))
        tool = get_tool_path("samtools" if is_alignment else "bcftools")
        if not tool:
            return {"problem": "headerToolMissing"}
        header = (
            get_bam_header(input_path)
            if is_alignment
            else run_command([tool, "view", "-h", input_path], capture_output=True).stdout
        )
        lengths = sequence_lengths(header)
        if not lengths:
            return {"problem": "referenceMissing"}
        library = ReferenceLibrary(
            None,
            md5_sig=sequence_signature(lengths),
            input_path=input_path,
            skip_full_search=True,
        )
        root = library_roots(None).root
        if not root:
            return {"problem": "referenceMissing"}
        directories = [Path(root), Path(reference_genomes_dir(root))]
        # The CLI can also search beside the input. The viewer serves only installed
        # libraries, and must not let an adjacent reference hide an installed match.
        candidates = list(
            dict.fromkeys(
                [library.fasta]
                + [
                    file
                    for directory in directories
                    for file in reference_fasta_candidates(directory)
                ]
            )
        )
        unverified = False
        sequences = header_sequences(header)
        for fasta in candidates:
            if not fasta or not any(
                Path(fasta).resolve().is_relative_to(directory.resolve())
                for directory in directories
            ):
                continue
            installed = read_fai_lengths(fasta)
            if not all(
                installed.get(name) == length for name, length in lengths.items()
            ):
                continue
            # Never compute a whole-genome dictionary or follow CRAM URs.
            if any(row.get("M5") for row in sequences):
                dictionary = Path(reference_dict_path(fasta))
                if not dictionary.is_file():
                    unverified = True
                    continue
                checksums = {
                    row.get("SN"): row.get("M5", "").lower()
                    for row in header_sequences(dictionary.read_text(encoding="utf-8"))
                }
                if any(
                    row.get("M5") and row["M5"].lower() != checksums.get(row.get("SN"))
                    for row in sequences
                ):
                    continue
            elif input_path.lower().endswith(".cram"):
                unverified = True
                continue
            first = next(iter(lengths))
            locus = (
                _default_locus(lengths, input_path, tool, is_alignment)
                if include_locus
                else _window_at(first, 1, lengths[first])
            )
            return {
                "fasta": fasta,
                "locus": locus,
            }
        return {"problem": "referenceUnverified" if unverified else "referenceMissing"}


if __name__ == "__main__":
    include_locus = "--no-locus" not in sys.argv[3:]
    print(
        json.dumps(
            resolve_reference(sys.argv[1], sys.argv[2], include_locus=include_locus)
        )
    )
