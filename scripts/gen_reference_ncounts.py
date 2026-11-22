"""Regenerate src/wgsextract_cli/assets/reference/reference_ncounts.tsv.

Measures the N bases of each genome in the catalog (seed_genomes.csv) from its
FASTA, found in the --source folders under its final, initial or download file
name. Lowercase (soft-masked) n bases count: WGS Extract v4's _ncnt.csv files
missed them in UCSC and NCBI references. A FASTA counts only if its sequences
give the catalog's signature; genomes with no matching FASTA keep their entry.

    pixi run python scripts/gen_reference_ncounts.py --source DIR [--source DIR]
        [--only NAME ...]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from wgsextract_cli.core.ref_library import ncount_table as table
from wgsextract_cli.core.ref_library.assets import sequence_signature
from wgsextract_cli.core.ref_library.ncount import count_sequence_ns

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/wgsextract_cli/assets/reference"


def catalog() -> dict[str, tuple[str, set[str]]]:
    """Final file name: (signature, the file names it may be stored under)."""
    genomes: dict[str, tuple[str, set[str]]] = {}
    with open(ASSETS / "seed_genomes.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            final = row["Final File Name"].strip()
            signature, names = genomes.setdefault(
                final, (row["Signature"].strip(), {final})
            )
            names.update({row["Initial File Name"].strip(), row["URL"].split("/")[-1]})
    return genomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()

    entries = {e.reference: e for e in table.packaged_table()}
    missing = False
    for final, (expected, names) in sorted(catalog().items()):
        if args.only is not None and final not in args.only:
            continue
        candidates = [
            folder / name for folder in args.source for name in sorted(names) if name
        ]
        for fasta in filter(Path.is_file, candidates):
            counts = count_sequence_ns(str(fasta)) or {}
            signature = sequence_signature({k: v[0] for k, v in counts.items()})
            if expected and signature != expected:
                print(f"{final}: {fasta} has signature {signature}, not {expected}")
                continue
            entries[final] = table.measured_entry(final, signature, counts)
            length, ns = entries[final].total
            print(f"{final}: {ns:,} N of {length:,} bp in {fasta}", flush=True)
            break
        else:
            print(f"{final}: no FASTA with its signature; entry kept", file=sys.stderr)
            missing = True

    (ASSETS / table.TABLE_NAME).write_text(
        table.format_table(entries.values()), encoding="utf-8"
    )
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
