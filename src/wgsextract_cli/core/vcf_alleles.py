"""Normalize the sequence parts of VCF alleles, not symbolic IDs or contigs."""

from __future__ import annotations

import re
import sys

_SEQUENCE = re.compile(r"^[ACGTNacgtn]+(?=$|[.\[\]])|(?<=[.\[\]])[ACGTNacgtn]+$")


def allele_key(allele: str) -> str:
    """Uppercase literal bases, including the local sequence of a breakend."""
    if allele.isupper():
        return allele
    return _SEQUENCE.sub(lambda match: match[0].upper(), allele)


def _uppercase_stream() -> None:
    # Only edit the two allele columns; symbolic IDs and non-allele fields are untouched.
    out = sys.stdout.buffer
    for line in sys.stdin.buffer:
        if not line.startswith(b"#"):
            fields = line.split(b"\t", 5)
            fields[3] = fields[3].upper()
            alt = fields[4].decode("utf-8", errors="surrogateescape")
            fields[4] = ",".join(map(allele_key, alt.split(","))).encode(
                "utf-8", errors="surrogateescape"
            )
            line = b"\t".join(fields)
        out.write(line)


if __name__ == "__main__":
    _uppercase_stream()
