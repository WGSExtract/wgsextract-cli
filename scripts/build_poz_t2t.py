"""Regenerate src/wgsextract_cli/assets/reference/poz/t2t.bed.

Lifts the bundled GRCh38 Poz chrY regions (poz/hg38.bed) to T2T-CHM13v2.0
(UCSC hs1) with the UCSC hg38ToHs1 liftOver chain. Each region is cut to the
chain's aligned blocks, so T2T sequence absent from GRCh38 (for example the
larger TSPY array) is not added. Blocks landing outside T2T chrY are dropped,
and lifted pieces closer than --merge-gap bases (small indels, shorter than a
read) are joined.

The chain is a development-time input, never fetched at runtime. Download it
once and pass it in; its SHA-256 is checked:

    curl -LO https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/hg38ToHs1.over.chain.gz
    pixi run python scripts/build_poz_t2t.py --chain hg38ToHs1.over.chain.gz

Record the printed SHA-256 in poz/NOTICE.txt.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

POZ = Path(__file__).resolve().parents[1] / "src/wgsextract_cli/assets/reference/poz"
CHAIN_URL = (
    "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/hg38ToHs1.over.chain.gz"
)
CHAIN_SHA256 = "926d4583eda8f90c88434293859b06906573f2469630e898b8b475ac8751327a"
CHRY_LENGTHS = {"hg38": 57_227_415, "t2t": 62_460_029}

Interval = tuple[int, int]
Block = tuple[int, int, int, bool]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chain_blocks(path: Path) -> Iterator[Block]:
    """Aligned chrY->chrY blocks: hg38 start, hg38 end, T2T start, T2T strand."""
    with gzip.open(path, "rt", encoding="ascii") as lines:
        keep = reverse = False
        source = target = 0
        for line in lines:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "chain":
                keep = (fields[2], fields[7]) == ("chrY", "chrY")
                sizes = int(fields[3]), int(fields[8])
                if keep and sizes != (CHRY_LENGTHS["hg38"], CHRY_LENGTHS["t2t"]):
                    sys.exit(f"unexpected chrY lengths in chain: {sizes}")
                if fields[4] != "+":
                    sys.exit("the chain's hg38 side must be on the plus strand")
                reverse = fields[9] == "-"
                source, target = int(fields[5]), int(fields[10])
                continue
            size = int(fields[0])
            if keep:
                yield source, source + size, target, reverse
            source += size + (int(fields[1]) if len(fields) == 3 else 0)
            target += size + (int(fields[2]) if len(fields) == 3 else 0)


def lift_piece(start: int, end: int, block: Block) -> Interval:
    """T2T plus-strand coordinates of [start, end) inside one block."""
    left, _, target, reverse = block
    start, end = target + start - left, target + end - left
    if reverse:
        return CHRY_LENGTHS["t2t"] - end, CHRY_LENGTHS["t2t"] - start
    return start, end


def lift(
    regions: Iterable[Interval], blocks: list[Block], merge_gap: int
) -> list[Interval]:
    pieces = sorted(
        lift_piece(max(start, block[0]), min(end, block[1]), block)
        for start, end in regions
        for block in blocks
        if max(start, block[0]) < min(end, block[1])
    )
    merged: list[list[int]] = []
    for start, end in pieces:
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chain", type=Path, required=True, help=CHAIN_URL)
    parser.add_argument("--output", type=Path, default=POZ / "t2t.bed")
    parser.add_argument("--merge-gap", type=int, default=100)
    args = parser.parse_args()

    if (digest := sha256(args.chain)) != CHAIN_SHA256:
        sys.exit(f"{args.chain}: SHA-256 {digest}, expected {CHAIN_SHA256}")
    source = POZ / "hg38.bed"
    regions = [
        (int(start), int(end))
        for _, start, end in (
            line.split("\t") for line in source.read_text("utf-8").splitlines()
        )
    ]
    lifted = lift(regions, list(chain_blocks(args.chain)), args.merge_gap)
    args.output.write_text(
        "".join(f"chrY\t{start}\t{end}\n" for start, end in lifted),
        encoding="utf-8",
        newline="\n",
    )
    for path, rows in ((source, regions), (args.output, lifted)):
        size = sum(end - start for start, end in rows)
        print(f"{path.name}: {len(rows)} regions, {size:,} bp, SHA-256 {sha256(path)}")


if __name__ == "__main__":
    main()
