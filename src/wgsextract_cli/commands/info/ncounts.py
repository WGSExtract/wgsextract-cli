"""The N bases of the reference an alignment was made against."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from wgsextract_cli.core.ref_library import ncount_table
from wgsextract_cli.core.ref_library.assets import (
    reference_genomes_dir,
    resolve_reference_library,
)

from .metrics import contig_key, load_n_counts


class ReferenceNs(NamedTuple):
    n_lengths: dict[str, int]  # N bases by `contig_key`; empty when unknown
    problem: tuple[str, str] | None  # warning (code, message) when unknown


def reference_n_lengths(
    lengths: Mapping[str, int], signature: str | None, reference: str | None
) -> ReferenceNs:
    """N counts of the header's reference, from the N-count table entry for its
    signature (and FASTA name, when two FASTAs share a signature), else from
    the given FASTA's own ``_ncnt.csv`` (ref count-ns). Only a contig of the
    measured length gets its count."""
    fasta = Path(reference) if reference and Path(reference).is_file() else None
    folders = [reference_genomes_dir(resolve_reference_library())]
    if fasta:
        folders.append(str(fasta.parent))
    table = ncount_table.load_table(folders)
    name = fasta.name if fasta else None
    matches = ncount_table.find_counts(table, signature, name) or [
        e for e in table if e.reference == name
    ]
    if len(matches) > 1:
        names = ", ".join(sorted(e.reference for e in matches))
        return ReferenceNs(
            {},
            (
                "n_counts_ambiguous",
                f"The reference matches several genomes with different N gaps "
                f"({names}), so depth and breadth include its N gaps; give the "
                "FASTA with --ref to choose.",
            ),
        )
    if matches:
        return ReferenceNs(
            {
                contig_key(seq): ns
                for seq, (length, ns) in matches[0].sequences.items()
                if lengths.get(seq) == length
            },
            None,
        )
    if counts := load_n_counts(str(fasta) if fasta else None):
        return ReferenceNs(counts, None)
    return ReferenceNs(
        {},
        (
            "n_counts_unavailable",
            "The reference's N counts are unknown, so depth and breadth include "
            "its N gaps; give its FASTA with --ref after running `wgsextract ref "
            "count-ns --ref FASTA`.",
        ),
    )


def n_lengths_digest(n_lengths: Mapping[str, int]) -> str:
    """Names the N counts a cached depth or breadth left out."""
    return hashlib.sha256(json.dumps(sorted(n_lengths.items())).encode()).hexdigest()


def alignment_n_lengths(alignment: str, reference: str | None) -> ReferenceNs:
    """`reference_n_lengths` for an alignment file's header."""
    from wgsextract_cli.core.alignment.metadata import get_bam_header
    from wgsextract_cli.core.ref_library.models import sequence_lengths
    from wgsextract_cli.core.variant_files import calculate_bam_md5

    header = get_bam_header(alignment, reference)
    signature = calculate_bam_md5(alignment, header=header) if header else None
    return reference_n_lengths(sequence_lengths(header or ""), signature, reference)
