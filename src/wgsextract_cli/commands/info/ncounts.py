"""The N bases of the reference an alignment was made against."""

import hashlib
import json
from collections import Counter
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
    lengths: Mapping[str, int], signature: str | None, selected: str | None
) -> ReferenceNs:
    """N counts of the header's reference, by `contig_key`; only a contig of
    the measured length gets its count, and none whose key two of the header's
    contigs share (chr1 and 1).

    For a ``selected`` FASTA (the user's --ref): its N-count table entry under
    the header's signature, else its own ``_ncnt.csv`` (ref count-ns), else its
    entry under its name alone (a subset header); never another genome's.
    Otherwise the table entry for the signature, unless genomes sharing it
    differ."""
    fasta = Path(selected) if selected and Path(selected).is_file() else None
    folders = [reference_genomes_dir(resolve_reference_library())]
    if fasta:
        folders.append(str(fasta.parent))
    table = ncount_table.load_table(folders)
    keys = Counter(contig_key(name) for name in lengths)

    def applied(counts: Mapping[str, int]) -> ReferenceNs:
        counts = {k: ns for k, ns in counts.items() if keys[k] == 1}
        return ReferenceNs(counts, None) if counts else _unavailable()

    def measured(entry: ncount_table.ReferenceNCounts) -> ReferenceNs:
        if not entry.sequences:  # a genome without Ns
            return ReferenceNs({}, None)
        return applied(
            {
                contig_key(seq): ns
                for seq, (length, ns) in entry.sequences.items()
                if lengths.get(seq) == length
            }
        )

    if fasta:
        named = [e for e in table if e.reference == fasta.name]
        exact = [e for e in named if e.signature == signature]
        if exact:
            return measured(exact[0])
        if Path(n_count_paths(str(fasta))[0]).is_file():
            return applied(load_n_counts(str(fasta), lengths))
        return measured(named[0]) if named else _unavailable()
    matches = ncount_table.find_counts(table, signature, None)
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
    return measured(matches[0]) if matches else _unavailable()


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
