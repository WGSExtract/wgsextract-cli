"""The N bases of the reference an alignment was made against."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from wgsextract_cli.core.ref_library import ncount_table
from wgsextract_cli.core.ref_library.assets import (
    n_count_paths,
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
    """N counts of the header's reference, by `contig_key`; only a contig of
    the measured length gets its count. In order: the N-count table entry for
    the ``--ref`` FASTA's name and the header's signature; that FASTA's own
    ``_ncnt.csv`` (ref count-ns); the one entry (or several identical ones)
    for the signature; an entry under the FASTA's name alone."""
    fasta = Path(reference) if reference and Path(reference).is_file() else None
    folders = [reference_genomes_dir(resolve_reference_library())]
    if fasta:
        folders.append(str(fasta.parent))
    table = ncount_table.load_table(folders)
    name = fasta.name if fasta else None

    def measured(entry: ncount_table.ReferenceNCounts) -> ReferenceNs:
        counts = {
            contig_key(seq): ns
            for seq, (length, ns) in entry.sequences.items()
            if lengths.get(seq) == length
        }
        if entry.sequences and not counts:  # none of its contigs are here
            return _unavailable()
        return ReferenceNs(counts, None)

    named = ncount_table.find_counts(table, signature, name)
    if named and named[0].reference == name:
        return measured(named[0])
    if fasta and Path(n_count_paths(str(fasta))[0]).is_file():
        return ReferenceNs(load_n_counts(str(fasta), lengths), None)
    if len(named) > 1:
        names = ", ".join(sorted(e.reference for e in named))
        return ReferenceNs(
            {},
            (
                "n_counts_ambiguous",
                f"The reference matches several genomes with different N gaps "
                f"({names}), so depth and breadth include its N gaps; give the "
                "FASTA with --ref to choose.",
            ),
        )
    by_name = [e for e in table if e.reference == name]
    if named or by_name:
        return measured((named or by_name)[0])
    return _unavailable()


def _unavailable() -> ReferenceNs:
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
