"""A tiny ancestry reference and a sample drawn from it, for `qc ancestry` tests.

A random 3-contig reference (chr1-chr3, standing in for GRCh38 with --build),
an ancestry reference .npz with SITES sites and random component frequencies
at K=7 and K=12, and one person whose alleles are drawn with known K=7
proportions; as reads (BAM), as a VCF and as a 23andMe-style raw file.
"""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
from pathlib import Path

import numpy as np

from tests.htslib_helpers import sam_to_bam

CONTIGS = ("chr1", "chr2", "chr3")
LENGTH = 400_000
SITES_PER_CONTIG = 1_000  # over 300 bp apart: a read covers one site
READ = 100
NAME = "wgsextract-ancestry-hgdp1kg-v1.npz"


def _components(k: int) -> dict:
    return {
        "name": f"Component {k}",
        "region": "AFR",
        "top_populations": {"Yoruba": 0.9},
    }


def build(folder: Path, proportions: list[float], seed: int = 11) -> dict:
    """The reference genome, the ancestry reference (in ``folder/ancestry``, a library beside the FASTA) and
    the person's genotypes (0-2 alt copies per site)."""
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    folder.mkdir(parents=True, exist_ok=True)
    seqs = {c: "".join(rng.choice("ACGT") for _ in range(LENGTH)) for c in CONTIGS}
    fasta = folder / "ref.fa"
    with fasta.open("w") as f:
        for contig, seq in seqs.items():
            f.write(f">{contig}\n")
            f.writelines(seq[i : i + 60] + "\n" for i in range(0, LENGTH, 60))
    subprocess.run(["samtools", "faidx", str(fasta)], check=True)

    chrom, pos, ref, alt = [], [], [], []
    step = (LENGTH - 2 * READ) // SITES_PER_CONTIG
    for c, contig in enumerate(CONTIGS, 1):
        for i in range(SITES_PER_CONTIG):
            p = READ + i * step + rng.randrange(step // 2)
            r = seqs[contig][p - 1]
            chrom.append(c)
            pos.append(p)
            ref.append(r)
            alt.append(rng.choice([b for b in "ACGT" if b != r]))
    n = len(pos)
    arrays: dict[str, np.ndarray] = {
        "chrom_GRCh38": np.array(chrom, dtype=np.uint8),
        "pos_GRCh38": np.array(pos, dtype=np.int32),
        "chrom_GRCh37": np.zeros(n, dtype=np.uint8),
        "pos_GRCh37": np.zeros(n, dtype=np.int32),
        "chrom_CHM13": np.zeros(n, dtype=np.uint8),
        "pos_CHM13": np.zeros(n, dtype=np.int32),
        "ref": np.array(ref, dtype="S1"),
        "alt": np.array(alt, dtype="S1"),
        "pca_frequency": np.full(n, 0.5, dtype=np.float32),
        "pca_loadings": np.zeros((n, 2), dtype=np.float32),
        "pca_scores": np.zeros((4, 2), dtype=np.float32),
        "pca_population": np.array(["A", "A", "B", "B"]),
        "pca_region": np.array(["AFR", "AFR", "EUR", "EUR"]),
    }
    frequencies = {}
    for k in (7, 12):
        p = nrng.beta(0.5, 0.5, size=(n, k))
        frequencies[k] = np.round(p * 65535) / 65535
        arrays[f"P{k}"] = np.round(p * 65535).astype(np.uint16)
    info = {
        "version": 1,
        "components": {
            str(k): [
                {**_components(j), "name": f"Component {j + 1} of {k}"}
                for j in range(k)
            ]
            for k in (7, 12)
        },
    }
    arrays["info"] = np.array(json.dumps(info))
    reflib = folder
    (reflib / "ancestry").mkdir()
    npz = reflib / "ancestry" / NAME
    with npz.open("wb") as f:
        np.savez_compressed(f, **arrays)  # type: ignore[arg-type]
    f_site = frequencies[7] @ np.array(proportions)
    genotypes = nrng.binomial(2, f_site)
    return {
        "fasta": fasta,
        "seqs": seqs,
        "reflib": reflib,
        "sha256": hashlib.sha256(npz.read_bytes()).hexdigest(),
        "sites": list(zip(chrom, pos, ref, alt, strict=True)),
        "genotypes": genotypes,
    }


def reads_bam(sample: dict, path: Path, depth: int, sites: int | None = None) -> Path:
    """An indexed BAM of single-end reads over the first ``sites`` sites."""
    rng = random.Random(5)
    records = []
    n = 0
    for (c, pos, _ref, alt), g in list(
        zip(sample["sites"], sample["genotypes"], strict=True)
    )[:sites]:
        contig = CONTIGS[c - 1]
        seq = sample["seqs"][contig]
        for _ in range(depth):
            carries = rng.random() < g / 2
            start = pos - rng.randrange(1, READ)
            read = list(seq[start - 1 : start - 1 + READ])
            if carries:
                read[pos - start] = alt
            n += 1
            records.append(
                f"r{n}\t0\t{contig}\t{start}\t60\t{READ}M\t*\t0\t0\t"
                f"{''.join(read)}\t{'I' * READ}\tRG:Z:rg"
            )
    header = ["@HD\tVN:1.6\tSO:unsorted"]
    header += [f"@SQ\tSN:{c}\tLN:{LENGTH}" for c in CONTIGS]
    header.append("@RG\tID:rg\tSM:S")
    return Path(sam_to_bam(header + records, path))


def calls_vcf(sample: dict, path: Path, *, variants_only: bool) -> Path:
    """A bgzipped, indexed single-sample VCF of the person's calls."""
    lines = [
        "##fileformat=VCFv4.2",
        *(f"##contig=<ID={c},length={LENGTH}>" for c in CONTIGS),
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS",
    ]
    for (c, pos, ref, alt), g in zip(sample["sites"], sample["genotypes"], strict=True):
        if variants_only and g == 0:
            continue
        gt = ("0/0", "0/1", "1/1")[g]
        lines.append(f"{CONTIGS[c - 1]}\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t.\tGT\t{gt}")
    plain = path.with_suffix("")
    plain.write_text("\n".join(lines) + "\n")
    subprocess.run(["bgzip", "-f", str(plain)], check=True)
    subprocess.run(["tabix", "-p", "vcf", str(path)], check=True)
    return path


def raw_23andme(sample: dict, path: Path) -> Path:
    lines = [
        "# This data file generated by 23andMe",
        "# rsid\tchromosome\tposition\tgenotype",
    ]
    for i, ((c, pos, ref, alt), g) in enumerate(
        zip(sample["sites"], sample["genotypes"], strict=True)
    ):
        lines.append(f"rs{i}\t{c}\t{pos}\t{(ref + ref, ref + alt, alt + alt)[g]}")
    path.write_text("\n".join(lines) + "\n")
    return path
