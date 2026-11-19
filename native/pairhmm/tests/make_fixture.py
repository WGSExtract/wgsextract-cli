# SPDX-License-Identifier: MIT
"""Small synthetic HaplotypeCaller SNP/insertion/deletion qualification input."""

import random
import subprocess
import sys
from pathlib import Path


def main() -> None:
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(1131)
    sequence = "".join(rng.choice("ACGT") for _ in range(5000))
    reference = output / "ref.fa"
    reference.write_text(">chr1\n" + sequence + "\n")
    rows = [
        "@HD\tVN:1.6\tSO:coordinate",
        "@SQ\tSN:chr1\tLN:5000",
        "@RG\tID:synthetic\tSM:synthetic\tPL:ILLUMINA\tLB:synthetic",
    ]
    for index, start in enumerate(range(1, 4850, 5)):
        bases = list(sequence[start - 1 : start + 149])
        cigar = "150M"
        if index % 2:
            if start <= 1000 < start + 150:
                offset = 1000 - start
                bases[offset] = "A" if bases[offset] != "A" else "C"
            if start < 2000 < start + 149:
                offset = 2000 - start + 1
                bases.insert(offset, "A")
                cigar = f"{offset}M1I{150 - offset}M"
            if start < 3000 < start + 149:
                offset = 3000 - start
                del bases[offset]
                cigar = f"{offset}M1D{149 - offset}M"
        rows.append(
            f"read{index}\t0\tchr1\t{start}\t60\t{cigar}\t*\t0\t0\t"
            f"{''.join(bases)}\t{'I' * len(bases)}\tRG:Z:synthetic"
        )
    sam = output / "reads.sam"
    sam.write_text("\n".join(rows) + "\n")
    subprocess.run(["samtools", "faidx", str(reference)], check=True)
    subprocess.run(
        ["samtools", "dict", "-o", str(output / "ref.dict"), str(reference)], check=True
    )
    subprocess.run(
        ["samtools", "view", "-b", "-o", str(output / "reads.bam"), str(sam)],
        check=True,
    )
    subprocess.run(["samtools", "index", str(output / "reads.bam")], check=True)
    sam.unlink()


if __name__ == "__main__":
    main()
