"""Compare GATK calls and complete reference blocks, allowing variant annotations."""

import gzip
import json
import sys
from collections.abc import Iterator
from itertools import zip_longest
from pathlib import Path


def records(path: Path) -> Iterator[list[str]]:
    with gzip.open(path, "rt") as handle:
        for line in handle:
            if not line.startswith("#"):
                yield line.rstrip("\n").split("\t")


def compare(java: Path, native: Path) -> dict[str, int]:
    result = {"records": 0, "reference_blocks": 0, "variant_annotation_differences": 0}
    for left, right in zip_longest(records(java), records(native)):
        if left is None or right is None:
            raise ValueError("Different record counts")
        # Sites, allele ordering and FILTER must match even when annotations differ.
        if [*left[:2], *left[3:5], left[6]] != [*right[:2], *right[3:5], right[6]]:
            raise ValueError(f"Different sites/alleles/FILTER at {left[:2]}")
        if len(left) != len(right):
            raise ValueError(f"Different sample counts at {left[:2]}")
        for a, b in zip(left[9:], right[9:], strict=True):
            if (
                a.split(":")[left[8].split(":").index("GT")]
                != b.split(":")[right[8].split(":").index("GT")]
            ):
                raise ValueError(f"Different genotypes at {left[:2]}")
        if left[4] == "<NON_REF>":
            result["reference_blocks"] += 1
            if left != right:
                raise ValueError(f"Different reference blocks at {left[:2]}")
        elif left != right:
            result["variant_annotation_differences"] += 1
        result["records"] += 1
    if not result["records"]:
        raise ValueError("Empty qualification input")
    return result


if __name__ == "__main__":
    print(json.dumps(compare(Path(sys.argv[1]), Path(sys.argv[2])), indent=2))
