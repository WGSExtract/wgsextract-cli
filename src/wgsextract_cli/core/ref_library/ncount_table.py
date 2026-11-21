"""Each reference genome's N (unsequenceable) bases, measured from its FASTA.

The counts never change for a given FASTA, so the release ships them for the
catalog's genomes in ``assets/reference/reference_ncounts.tsv``, read at run
time so a release can add genomes without code changes. A genome processed
into a library that the table lacks is measured then, and its counts go into
that library's own ``reference_ncounts.tsv``, which is read alongside.

An entry is keyed by the FASTA's file name and its sequence-dictionary
signature: FASTAs with one signature can still mask different regions with N
(hs38d1 and GRCh38_Verily_v1), so the signature alone doesn't name the counts.
Rows list each sequence with N bases; a ``*`` row gives the genome's totals,
so a genome without Ns still has an entry.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from wgsextract_cli.core.atomic_output import atomic_write
from wgsextract_cli.core.paths import PathInput

TABLE_NAME = "reference_ncounts.tsv"
_COLUMNS = ("reference", "signature", "sequence", "length", "n_bases")
_HEADER = (
    "# N bases per reference genome, measured from each FASTA (ref count-ns).\n"
    "# One row per sequence with N bases; '*' gives the genome's length and N "
    "total.\n#" + "\t".join(_COLUMNS) + "\n"
)


@dataclass(frozen=True)
class ReferenceNCounts:
    reference: str  # the FASTA's file name, e.g. "hs38DH.fa.gz"
    signature: str  # its SN/LN sequence-dictionary signature
    sequences: Mapping[str, tuple[int, int]]  # name: (length, N bases), N > 0
    total: tuple[int, int]  # genome length and N bases

    @property
    def key(self) -> tuple[str, str]:
        return self.reference, self.signature


def _parse(text: str) -> list[ReferenceNCounts]:
    grouped: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
    for row in csv.reader(io.StringIO(text), delimiter="\t"):
        if not row or row[0].startswith("#"):
            continue
        reference, signature, sequence, length, n_bases = row
        grouped.setdefault((reference, signature), {})[sequence] = (
            int(length),
            int(n_bases),
        )
    return [
        ReferenceNCounts(
            reference, signature, {k: v for k, v in rows.items() if k != "*"}, total
        )
        for (reference, signature), rows in grouped.items()
        if (total := rows.get("*")) is not None
    ]


def packaged_table() -> list[ReferenceNCounts]:
    text = (
        resources.files("wgsextract_cli")
        .joinpath(f"assets/reference/{TABLE_NAME}")
        .read_text(encoding="utf-8")
    )
    return _parse(text)


def library_table(genomes_dir: PathInput) -> list[ReferenceNCounts]:
    """The entries a library added for genomes the release didn't measure."""
    path = Path(genomes_dir, TABLE_NAME)
    try:
        return _parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError, ValueError) as e:
        logging.warning(f"Ignoring unreadable N-count table {path}: {e}")
        return []


def load_table(genomes_dirs: Iterable[PathInput] = ()) -> list[ReferenceNCounts]:
    """The packaged entries, then the given libraries' own (which never replace
    a packaged entry: the same FASTA has the same Ns)."""
    entries = {e.key: e for e in packaged_table()}
    for folder in genomes_dirs:
        for entry in library_table(folder):
            entries.setdefault(entry.key, entry)
    return list(entries.values())


def format_table(entries: Iterable[ReferenceNCounts]) -> str:
    lines = [_HEADER]
    for e in sorted(entries, key=lambda e: e.key):
        rows = [*sorted(e.sequences.items()), ("*", e.total)]
        lines += [
            f"{e.reference}\t{e.signature}\t{name}\t{length}\t{ns}\n"
            for name, (length, ns) in rows
        ]
    return "".join(lines)


def measured_entry(
    reference: str, signature: str, counts: Mapping[str, tuple[int, int]]
) -> ReferenceNCounts:
    """An entry from `ncount.count_sequence_ns` output."""
    return ReferenceNCounts(
        reference,
        signature,
        {name: c for name, c in counts.items() if c[1]},
        (sum(c[0] for c in counts.values()), sum(c[1] for c in counts.values())),
    )


def add_to_library(genomes_dir: PathInput, entry: ReferenceNCounts) -> None:
    """Record a genome's counts in its library's table."""
    entries = {e.key: e for e in library_table(genomes_dir)}
    entries[entry.key] = entry
    atomic_write(
        Path(genomes_dir, TABLE_NAME),
        format_table(entries.values()).encode(),
        new_mode=0o666,
    )


def find_counts(
    table: Iterable[ReferenceNCounts], signature: str | None, fasta_name: str | None
) -> list[ReferenceNCounts]:
    """The entries that could describe an alignment with this header
    `signature`, made against `fasta_name` if known: one entry, or several
    whose N counts differ (the caller can't choose), or none."""
    matches = [e for e in table if signature and e.signature == signature]
    named = [e for e in matches if e.reference == fasta_name]
    if named:
        return named[:1]
    distinct = {tuple(sorted(e.sequences.items())): e for e in matches}
    return list(distinct.values())
