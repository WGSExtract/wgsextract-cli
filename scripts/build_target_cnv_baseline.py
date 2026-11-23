"""Build offline target-CNV assets from local, pinned GIAB v3.5 tracks."""

import argparse
import gzip
import hashlib
import random
from pathlib import Path

from wgsextract_cli.commands.genotyping.target_cnv_data import (
    ASSEMBLIES,
    AUTOSOMES,
    IntervalMask,
    giab_tracks,
    mask_rows,
)
from wgsextract_cli.commands.info.metrics import contig_key
from wgsextract_cli.core.panel import load_annotations

MASK_BIN = 1000
WINDOW_SIZE = 1000
WINDOWS_PER_CHROMOSOME = 10


def _write_assets(build: str, tracks: Path, output: Path) -> None:
    paths = []
    for filename, url, digest in giab_tracks(build):
        path = tracks / filename
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise SystemExit(
                f"Need the pinned file {path} from {url} (SHA-256 {digest})"
            )
        paths.append(path)

    lengths = {
        contig_key(chrom): length
        for chrom, length in load_annotations(build).contigs.items()
    }
    mask = IntervalMask.from_rows(
        (chrom, start // MASK_BIN * MASK_BIN, min(
            lengths[chrom], (end + MASK_BIN - 1) // MASK_BIN * MASK_BIN
        ))
        for path in paths
        for chrom, start, end in mask_rows(path)
        if chrom in lengths and start < lengths[chrom]
    )  # fmt: skip
    with (
        (output / f"{build}.mask.bed.gz").open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as out,
    ):
        out.write(
            b"# GIAB v3.5 segmental duplications and low mappability, rounded outward to 1 kb\n"
        )
        for chrom in (*AUTOSOMES, "X", "Y"):
            for start, end in mask.intervals.get(chrom, []):
                out.write(f"chr{chrom}\t{start}\t{end}\n".encode())

    rng = random.Random(1285)
    with (output / f"{build}.baseline.bed").open(
        "w", encoding="utf-8", newline="\n"
    ) as out:
        out.write("# 1 kb autosomal windows outside the bundled GIAB v3.5 mask\n")
        for chrom in AUTOSOMES:
            starts: list[int] = []
            for i in range(WINDOWS_PER_CHROMOSOME):
                # Sample across the chromosome, not a single depth hotspot.
                left = lengths[chrom] * i // WINDOWS_PER_CHROMOSOME
                right = lengths[chrom] * (i + 1) // WINDOWS_PER_CHROMOSOME
                choices = [
                    p
                    for p in range(left, right - WINDOW_SIZE + 1, WINDOW_SIZE)
                    if not mask.overlaps(chrom, p, p + WINDOW_SIZE)
                ]
                if choices:
                    starts.append(rng.choice(choices))
            if len(starts) < WINDOWS_PER_CHROMOSOME // 2:
                raise SystemExit(f"Too few clean {build} windows on chr{chrom}")
            for start in starts:
                out.write(f"chr{chrom}\t{start}\t{start + WINDOW_SIZE}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tracks", type=Path, help="Folder with the six original BED.gz files"
    )
    parser.add_argument("output", type=Path, help="Destination for the derived assets")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for build in ASSEMBLIES:
        _write_assets(build, args.tracks, args.output)


if __name__ == "__main__":
    main()
