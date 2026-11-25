"""Build the panel audit's CC0 expected synonymous counts from gnomAD v4.1.

Pass the local gnomAD constraint TSV and an output path ending in .tsv.gz.
The source's exact SHA-256 is checked. The derived asset contains only
versioned RefSeq MANE Select accessions and gnomAD's synonymous expectation.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import math
import re
from pathlib import Path

SOURCE = (
    "https://storage.googleapis.com/gcp-public-data--gnomad/release/4.1/constraint/"
    "gnomad.v4.1.constraint_metrics.tsv"
)
SOURCE_SHA256 = "68d8abdb7fc48f570869b02dfaa74b9fecaece7fcc5f301ddca40ec1ce12da00"
_REFSEQ = re.compile(r"NM_[0-9]+\.[0-9]+")


def build(source: Path, output: Path) -> int:
    digest = hashlib.sha256()
    with source.open("rb") as raw:
        for data in iter(lambda: raw.read(1 << 20), b""):
            digest.update(data)
    if digest.hexdigest() != SOURCE_SHA256:
        raise SystemExit(f"{source}: does not match the gnomAD v4.1 SHA-256.")
    rows: dict[str, str] = {}
    with source.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            transcript, expected = row["transcript"], row["syn.exp"]
            if row["mane_select"] != "true" or not _REFSEQ.fullmatch(transcript):
                continue
            if expected in {"", "NA"}:
                continue
            value = float(expected)
            if not math.isfinite(value) or value < 0:
                raise SystemExit(
                    f"{transcript}: invalid synonymous expectation {expected!r}."
                )
            if transcript in rows:
                raise SystemExit(f"{transcript}: duplicated MANE Select accession.")
            rows[transcript] = expected
    if not rows:
        raise SystemExit("No gnomAD MANE Select synonymous expectations found.")
    with (
        output.open("wb") as destination,
        gzip.GzipFile(
            filename="", fileobj=destination, mode="wb", mtime=0
        ) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as stream,
    ):
        stream.write(
            "# gnomAD v4.1 constraint, MANE Select synonymous expectation (CC0 1.0)\n"
            f"# {SOURCE}\n"
            f"# upstream_sha256={SOURCE_SHA256}\n"
            "transcript\texpected_syn\n"
        )
        for transcript, expected in sorted(rows.items()):
            stream.write(f"{transcript}\t{expected}\n")
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    count = build(args.source, args.output)
    print(f"{args.output}: {count:,} transcript expectations")
