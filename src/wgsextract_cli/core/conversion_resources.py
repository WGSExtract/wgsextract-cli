"""Standalone alignment conversion budgets, including Windows reference pages."""

import platform
from pathlib import Path

from .ref_library.assets import read_fai_lengths
from .region_selection import selected_intervals

CRAM_BYTES_PER_READ = 28
BAM_BYTES_PER_READ = 100


def conversion_resources(
    threads: int,
    reads: int | None,
    reference: str | None,
    region: str | None,
    operation: str,
    notes: list[str],
) -> tuple[dict[str, int], int | None, bool]:
    memory = {"codecs": (threads + 1) * 64 * 1024**2, "overhead": 1024**3 // 4}
    output = (
        None
        if reads is None
        else reads
        * (CRAM_BYTES_PER_READ if operation == "to-cram" else BAM_BYTES_PER_READ)
    )
    if operation != "to-cram" or platform.system() != "Windows" or not reference:
        return memory, output, False
    # The full-HG002 mimalloc encoder retained up to 2.84 GiB at -@ 7.
    lengths = read_fai_lengths(reference)
    if lengths:
        contigs = selected_intervals(region, lengths) if region else lengths
        memory["reference_cache"] = sum(lengths[name] for name in contigs)
        return memory, output, False
    if reference.lower().endswith((".gz", ".bgz", ".bgzf")):
        notes.append(
            "Reference .fai unavailable: Windows CRAM reference-cache RAM is unknown."
        )
        return memory, output, True
    try:
        memory["reference_cache"] = Path(reference).stat().st_size
    except OSError:
        notes.append(
            "Reference size unavailable: Windows CRAM reference-cache RAM is unknown."
        )
        return memory, output, True
    notes.append(
        "Reference .fai unavailable: Windows CRAM cache conservatively uses FASTA file bytes."
    )
    return memory, output, False
