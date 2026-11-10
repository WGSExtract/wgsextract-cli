"""A synthetic population on a random reference, for VerifyBamID2 tests.

A random chr20 with common SNPs; a panel VCF of PANEL people drawn from two
ancestries with different allele frequencies; and paired reads of one person
of the panel, optionally mixed with another's at a fraction.
"""

from __future__ import annotations

import gzip
import random
import subprocess
from pathlib import Path

from tests.htslib_helpers import sam_to_bam

CONTIG = "chr20"
LENGTH = 1_200_000  # over 200 bp between sites: a read covers only one
SITES = 5000  # VerifyBamID2 needs at least 5,000 panel markers
PANEL = 1000
READ = 100
DEPTH = 20


def _reference(folder: Path, rng: random.Random) -> tuple[Path, str]:
    seq = "".join(rng.choice("ACGT") for _ in range(LENGTH))
    fasta = folder / "ref.fa"
    lines = [seq[i : i + 60] for i in range(0, LENGTH, 60)]
    fasta.write_text(f">{CONTIG}\n" + "\n".join(lines) + "\n")
    subprocess.run(["samtools", "faidx", str(fasta)], check=True)
    return fasta, seq


def population(folder: Path, seed: int = 7) -> dict:
    """Reference, panel VCF and each person's genotypes (0-2 alt copies)."""
    rng = random.Random(seed)
    folder.mkdir(parents=True, exist_ok=True)
    fasta, seq = _reference(folder, rng)
    step = (LENGTH - 2 * READ) // SITES
    sites = []
    for i in range(SITES):
        pos = READ + i * step + rng.randrange(step // 2)
        ref = seq[pos - 1]
        alt = rng.choice([b for b in "ACGT" if b != ref])
        base = rng.uniform(0.05, 0.95)
        afs = (base, min(max(base + rng.uniform(-0.3, 0.3), 0.02), 0.98))
        sites.append((pos, ref, alt, afs))
    people = []
    for p in range(PANEL):
        group = p % 2
        people.append(
            [[int(rng.random() < s[3][group]) for _ in range(2)] for s in sites]
        )
    vcf = folder / "panel.vcf.gz"
    with gzip.open(vcf, "wt") as f:
        f.write("##fileformat=VCFv4.2\n")
        f.write(f"##contig=<ID={CONTIG},length={LENGTH}>\n")
        f.write('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n')
        names = "\t".join(f"P{p}" for p in range(PANEL))
        f.write(f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{names}\n")
        for i, (pos, ref, alt, _afs) in enumerate(sites):
            gts = "\t".join(f"{h[i][0]}|{h[i][1]}" for h in people)
            f.write(f"{CONTIG}\t{pos}\trs{i}\t{ref}\t{alt}\t.\tPASS\t.\tGT\t{gts}\n")
    return {"fasta": fasta, "seq": seq, "sites": sites, "people": people, "vcf": vcf}


def build_panel(pop: dict, command: list[str]) -> str:
    """VerifyBamID2's SVD files for the panel; returns their prefix."""
    subprocess.run(
        [*command, "--RefVCF", str(pop["vcf"]), "--Reference", str(pop["fasta"])],
        check=True,
        capture_output=True,
    )
    return str(pop["vcf"])


def reads_bam(
    pop: dict,
    path: Path,
    person: int,
    other: int | None = None,
    fraction: float = 0.0,
    seed: int = 3,
) -> Path:
    """An indexed BAM of single-end reads over every site from ``person``,
    with ``fraction`` of them from ``other``."""
    rng = random.Random(seed)
    seq = pop["seq"]
    records = []
    n = 0
    for i, (pos, _ref, alt, _afs) in enumerate(pop["sites"]):
        for _ in range(DEPTH):
            who = other if other is not None and rng.random() < fraction else person
            allele = pop["people"][who][i][rng.randrange(2)]
            start = pos - rng.randrange(1, READ)
            read = list(seq[start - 1 : start - 1 + READ])
            if allele:
                read[pos - start] = alt
            n += 1
            records.append(
                f"r{n}\t0\t{CONTIG}\t{start}\t60\t{READ}M\t*\t0\t0\t"
                f"{''.join(read)}\t{'I' * READ}\tRG:Z:rg"
            )
    header = [
        "@HD\tVN:1.6\tSO:unsorted",
        f"@SQ\tSN:{CONTIG}\tLN:{len(seq)}",
        "@RG\tID:rg\tSM:S",
    ]
    return Path(sam_to_bam(header + records, path))
