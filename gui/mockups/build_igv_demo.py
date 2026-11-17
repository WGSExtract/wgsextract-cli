#!/usr/bin/env python3
"""Write shared/data/igv/: the v2 mockup genome viewer's demo data.

Simulated reads for the McDonald family and Alex in ten 10 kb windows of real
GRCh38 sequence plus the whole mitochondrial genome, so igv.js can show real
genes, rsIDs and haplogroup markers without shipping real genomes. Every
variant is planted below; nothing comes from a real person.

    python3 gui/mockups/build_igv_demo.py --ref path/to/hs38.fa.gz

Needs samtools, bgzip and tabix on PATH (or in --tools) and
an indexed GRCh38 FASTA with UCSC names (chr1, chrM = rCRS). Output is
deterministic for a given reference and is about 1.5 MB.
"""

from __future__ import annotations

import argparse
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "shared/data/igv"
READ = 150
SEED = 20261113

LENGTHS = {
    "chr1": 248956422,
    "chr2": 242193529,
    "chr5": 181538259,
    "chr6": 170805979,
    "chr17": 83257441,
    "chr19": 58617616,
    "chr20": 64444167,
    "chrY": 57227415,
    "chrM": 16569,
}

# The windows, 1-based inclusive: [chrom, start, end]
WINDOWS = {
    "mthfr": ("chr1", 11791322, 11801321),
    "lct": ("chr2", 135846077, 135856076),
    "smn1": ("chr5", 70940001, 70950000),
    "hfe": ("chr6", 26087914, 26097913),
    "brca1": ("chr17", 43066078, 43076077),
    "apoe": ("chr19", 44903685, 44913684),
    "denovo": ("chr20", 32000001, 32010000),
    "artifact": ("chr20", 45000001, 45010000),
    "deletion": ("chr20", 52000001, 52010000),
    "y": ("chrY", 20582968, 20592967),
    "mt": ("chrM", 1, 16569),
}

PEOPLE = {
    # sample name, sex, mtDNA set, output file (named as in the mockup's
    # library; Gordon's is the GRCh38 copy his realign job makes)
    "reginald": (
        "reginald-mcdonald",
        "male",
        "K1a1b1a",
        "reginald-mcdonald.GRCh38.30x.cram",
    ),
    "gordon": ("60820188479016", "male", "H1c", "60820188479016_aligned.cram"),
    "helen": (
        "helen-mcdonald",
        "female",
        "K1a1b1a",
        "helen-mcdonald.deepvariant.vcf.gz",
    ),
    "alex": ("NG1A2B3C4D", "female", "H1", "NG1A2B3C4D.bam"),
}

# Planted small variants: (chrom, pos, ref, alt, {person: genotype}). ref is
# checked against the reference. Genotype is copies of alt (0, 1 or 2).
SNVS = [
    ("chr19", 44908684, "T", "C", {"reginald": 1, "helen": 1}),  # APOE rs429358
    (
        "chr2",
        135851076,
        "G",
        "A",
        {"reginald": 1, "gordon": 2, "alex": 1},
    ),  # LCT rs4988235
    ("chr6", 26092913, "G", "A", {"reginald": 1, "gordon": 1}),  # HFE C282Y rs1800562
    (
        "chr1",
        11796321,
        "G",
        "A",
        {"reginald": 1, "gordon": 1, "helen": 1, "alex": 2},
    ),  # MTHFR rs1801133
    (
        "chr17",
        43071077,
        "T",
        "C",
        {"reginald": 1, "helen": 1, "alex": 1},
    ),  # BRCA1 rs1799966, benign
    (
        "chrY",
        20587967,
        "A",
        "C",
        {"reginald": 1, "gordon": 1},
    ),  # M304, haplogroup J (haploid)
    ("chr20", 32005123, None, None, {"reginald": 1}),  # de novo: new in Reginald
]
# A 4 bp deletion Reginald got from Helen: (chrom, pos before, deleted bases)
SMALL_DEL = ("chr20", 52007480, 4, {"reginald": 1, "helen": 1})
# A 1.2 kb deletion Reginald got from Gordon
BIG_DEL = ("chr20", 52003001, 1200, {"reginald": 1, "gordon": 1})
# A strand-bias artifact in Reginald: alt only on reverse reads, low quality
ARTIFACT = ("chr20", 45004321, 0.7)
# SMN1/SMN2: reads in the copied stretch map equally well to both genes
SMN_MAPQ0 = ("chr5", 70942001, 70948000)
SMN_PARALOG = [70943210, 70944877, 70946102]

# mtDNA differences from rCRS (which is haplogroup H2a2a1) by haplogroup
MT_COMMON = "263G 750G 1438G 4769G 8860G 15326G"
MT = {
    "H1": MT_COMMON + " 3010A",
    "H1c": MT_COMMON + " 3010A 477C",
    "K1a1b1a": MT_COMMON + " 73G 114T 497T 1189C 1811G 2706G 3480G 7028T 9055A"
    " 9698C 10398G 10550G 10978G 11299C 11467G 11470G 11719A 11914A 12308G"
    " 12372A 12954C 14167T 14766T 14798C 15924G 16093C 16224C 16234T 16311C",
}

# Genes for the viewer's annotation track, GRCh38 (0-based BED; approximate
# spans, clipped to the windows by the viewer). chrM genes follow rCRS.
GENES = [
    ("chr1", 11785722, 11806455, "MTHFR", "-"),
    ("chr2", 135839625, 135876443, "MCM6", "-"),
    ("chr5", 70924940, 70953015, "SMN1", "+"),
    ("chr6", 26087280, 26098343, "HFE", "+"),
    ("chr17", 43044294, 43125483, "BRCA1", "-"),
    ("chr19", 44905795, 44909393, "APOE", "+"),
    ("chrM", 0, 576, "Control region", "+"),
    ("chrM", 647, 1601, "MT-RNR1", "+"),
    ("chrM", 1670, 3229, "MT-RNR2", "+"),
    ("chrM", 3306, 4262, "MT-ND1", "+"),
    ("chrM", 4469, 5511, "MT-ND2", "+"),
    ("chrM", 5903, 7445, "MT-CO1", "+"),
    ("chrM", 7585, 8269, "MT-CO2", "+"),
    ("chrM", 8526, 9207, "MT-ATP6", "+"),
    ("chrM", 9206, 9990, "MT-CO3", "+"),
    ("chrM", 10058, 10404, "MT-ND3", "+"),
    ("chrM", 10759, 12137, "MT-ND4", "+"),
    ("chrM", 12336, 14148, "MT-ND5", "+"),
    ("chrM", 14148, 14673, "MT-ND6", "-"),
    ("chrM", 14746, 15887, "MT-CYB", "+"),
    ("chrM", 16023, 16569, "Control region", "+"),
]

QUALS = "#-8F"  # NovaSeq bins Q2, Q12, Q23, Q37
TRANSITION = {"A": "G", "G": "A", "C": "T", "T": "C"}


@dataclass
class Hap:
    """One haplotype of a window: its sequence and each base's reference
    position (None for inserted bases)."""

    seq: list[str]
    pos: list[int | None]
    alt_at: set[int] = field(default_factory=set)


def run(*cmd: str, **kw: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(cmd, check=True, **kw)


def fetch(ref: Path, tools: Path | None, chrom: str, start: int, end: int) -> str:
    out = run(
        tool(tools, "samtools"),
        "faidx",
        str(ref),
        f"{chrom}:{start}-{end}",
        capture_output=True,
        text=True,
    ).stdout
    return "".join(out.split("\n")[1:]).upper()


def tool(tools: Path | None, name: str) -> str:
    path = str(tools / name) if tools else shutil.which(name)
    if not path or not Path(path).exists():
        sys.exit(f"{name} not found: put it on PATH or pass --tools")
    return path


def haplotypes(
    person: str, win: str, ref: str, start: int, rng: random.Random
) -> list[Hap]:
    chrom = WINDOWS[win][0]
    sex = PEOPLE[person][1]
    if chrom == "chrY" and sex == "female":
        return []
    copies = 1 if chrom in ("chrY", "chrM") else 2
    haps = [Hap(list(ref), list(range(start, start + len(ref)))) for _ in range(copies)]

    def put(h: int, pos: int, base: str) -> None:
        i = pos - start
        haps[h].seq[i] = base
        haps[h].alt_at.add(pos)

    for c, pos, r, a, gts in SNVS:
        if c != chrom or not (start <= pos < start + len(ref)):
            continue
        base = ref[pos - start]
        if r is None or a is None:
            r, a = base, TRANSITION[base]
        if base != r:
            sys.exit(f"{c}:{pos} is {base} in the reference, not {r}")
        for h in range(min(gts.get(person, 0), copies)):
            put(h, pos, a)
    if chrom == "chrM":
        for change in MT[PEOPLE[person][2]].split():
            pos, alt = int(change[:-1]), change[-1]
            if ref[pos - 1] != alt:
                put(0, pos, alt)
    for c, after, n, gts in (SMALL_DEL, BIG_DEL):
        if c != chrom or not (start <= after < start + len(ref)) or not gts.get(person):
            continue
        # The deletion goes on the second haplotype (the other parent's)
        hap = haps[1 if (c, after, n, gts) == SMALL_DEL else 0]
        i = after - start + 1
        del hap.seq[i : i + n]
        del hap.pos[i : i + n]
    return haps


def quals(rng: random.Random) -> list[str]:
    """Binned qualities as a NovaSeq writes them: mostly Q37, with the odd
    lower base, more of them toward the end of the read."""
    out = ["F"] * READ
    for i in range(READ):
        if rng.random() < 0.01 + 0.06 * (i / READ) ** 2:
            out[i] = rng.choice("8888-#")
    return out


def cigar_of(positions: list[int | None]) -> tuple[int, str]:
    """The leftmost reference position and CIGAR of a read whose bases sit at
    these reference positions. A gap longer than 50 bp is soft-clipped on
    the shorter side, as an aligner would."""
    known = [(i, p) for i, p in enumerate(positions) if p is not None]
    first, start = known[0]
    ops: list[list[Any]] = []

    def add(op: str, n: int = 1) -> None:
        if ops and ops[-1][0] == op:
            ops[-1][1] += n
        else:
            ops.append([op, n])

    big = next(
        (
            (i, b)
            for (j, a), (i, b) in zip(known, known[1:], strict=False)
            if i == j + 1 and b - a - 1 > 50
        ),
        None,
    )
    if big:
        i, at = big
        if i < len(positions) - i:
            return at, f"{i}S{len(positions) - i}M"
        return start, f"{i}M{len(positions) - i}S"
    if first:
        add("S", first)
    prev = None
    for p in positions[first:]:
        if p is None:
            add("I")
            continue
        if prev is not None and p - prev > 1:
            add("D", p - prev - 1)
        add("M")
        prev = p
    return start, "".join(f"{n}{op}" for op, n in ops)


def reads(person: str, win: str, ref: str, rng: random.Random) -> list[str]:
    chrom, start, end = WINDOWS[win]
    haps = haplotypes(person, win, ref, start, rng)
    if not haps:
        return []
    depth = 60 if chrom == "chrM" else 30
    per_hap = depth / 2 if len(haps) == 2 else (depth / 2 if chrom == "chrY" else depth)
    sample = PEOPLE[person][0]
    lines = []
    n = 0
    for hap in haps:
        count = int(len(hap.seq) * per_hap / (2 * READ))
        for _ in range(count):
            frag = max(2 * READ - 20, int(rng.gauss(420, 60)))
            if len(hap.seq) <= frag:
                continue
            s = rng.randrange(0, len(hap.seq) - frag)
            fseq = hap.seq[s : s + frag]
            fpos = hap.pos[s : s + frag]
            n += 1
            name = f"{sample}:{win}:{n}"
            mates: list[tuple[int, str, str, str, bool, int]] = []
            for mate, (seq, pos) in enumerate(
                ((fseq[:READ], fpos[:READ]), (fseq[-READ:], fpos[-READ:]))
            ):
                seq = list(seq)
                qual = quals(rng)
                for i in range(READ):
                    if rng.random() < 0.002:
                        seq[i] = rng.choice([b for b in "ACGT" if b != seq[i]])
                        qual[i] = rng.choice("#-")
                reverse = mate == 1
                if (
                    person == "reginald"
                    and chrom == ARTIFACT[0]
                    and reverse
                    and ARTIFACT[1] in pos
                    and rng.random() < ARTIFACT[2]
                ):
                    i = pos.index(ARTIFACT[1])
                    seq[i] = TRANSITION[ref[ARTIFACT[1] - start]]
                    qual[i] = "-"
                mapq = 60
                if (
                    chrom == SMN_MAPQ0[0]
                    and pos[0] is not None
                    and SMN_MAPQ0[1] <= pos[0] <= SMN_MAPQ0[2]
                ):
                    mapq = 0
                    for p in SMN_PARALOG:
                        if p in pos and rng.random() < 0.35:
                            seq[pos.index(p)] = TRANSITION[ref[p - start]]
                at, cigar = cigar_of(pos)
                mates.append((at, cigar, "".join(seq), "".join(qual), reverse, mapq))
            (p1, c1, s1, q1, _, m1), (p2, c2, s2, q2, _, m2) = mates
            far = max(p1, p2) + READ
            tlen = far - min(p1, p2)
            proper = 0x2 if tlen < 1000 else 0
            # Read 1 forward, read 2 reverse
            f1 = 0x1 | proper | 0x20 | 0x40
            f2 = 0x1 | proper | 0x10 | 0x80
            rg = "\tRG:Z:" + sample
            lines.append(
                f"{name}\t{f1}\t{chrom}\t{p1}\t{m1}\t{c1}\t=\t{p2}\t{tlen}\t{s1}\t{q1}{rg}"
            )
            lines.append(
                f"{name}\t{f2}\t{chrom}\t{p2}\t{m2}\t{c2}\t=\t{p1}\t{-tlen}\t{s2}\t{q2}{rg}"
            )
    return lines


def header(sample: str) -> str:
    sq = "".join(f"@SQ\tSN:{c}\tLN:{n}\n" for c, n in LENGTHS.items())
    return (
        "@HD\tVN:1.6\tSO:coordinate\n"
        + sq
        + f"@RG\tID:{sample}\tSM:{sample}\tPL:ILLUMINA\tLB:{sample}\n"
        "@PG\tID:wgse-demo\tPN:build_igv_demo.py\tCL:simulated reads for the WGS Extract mockups\n"
    )


def vcf(person: str, refs: dict[str, str]) -> str:
    """The person's calls in the windows, DeepVariant style."""
    rows: list[tuple[str, int, str | None, str | None, str]] = []
    for c, pos, r, a, gts in SNVS:
        gt = gts.get(person, 0)
        if not gt:
            continue
        rows.append((c, pos, r, a, "0/1" if gt == 1 else "1/1"))
    c, after, n, gts = SMALL_DEL
    if gts.get(person):
        win = next(k for k, w in WINDOWS.items() if w[0] == c and w[1] <= after <= w[2])
        seq = refs[win][after - WINDOWS[win][1] : after - WINDOWS[win][1] + n + 1]
        rows.append((c, after, seq, seq[0], "0/1"))
    for change in MT[PEOPLE[person][2]].split():
        pos, alt = int(change[:-1]), change[-1]
        r = refs["mt"][pos - 1]
        if r != alt:
            rows.append(("chrM", pos, r, alt, "1/1"))
    order = list(LENGTHS)
    rows.sort(key=lambda x: (order.index(x[0]), x[1]))
    sample = PEOPLE[person][0]
    head = [
        "##fileformat=VCFv4.2",
        '##FILTER=<ID=PASS,Description="All filters passed">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">',
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Reads for each allele">',
        "##source=build_igv_demo.py (simulated for the WGS Extract mockups)",
        *(f"##contig=<ID={c},length={n}>" for c, n in LENGTHS.items()),
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + sample,
    ]
    rng = random.Random(f"{SEED}{person}vcf")
    body = []
    for c, pos, r, a, geno in rows:
        dp = rng.randint(250, 330) if c == "chrM" else rng.randint(24, 36)
        n_alt = dp if geno == "1/1" else dp // 2 + rng.randint(-3, 3)
        if r is None or a is None:
            win = next(
                k for k, w in WINDOWS.items() if w[0] == c and w[1] <= pos <= w[2]
            )
            r = refs[win][pos - WINDOWS[win][1]]
            a = TRANSITION[r]
        body.append(
            f"{c}\t{pos}\t.\t{r}\t{a}\t{rng.randint(38, 62)}\tPASS\t.\tGT:GQ:DP:AD\t{geno}:{rng.randint(40, 60)}:{dp}:{dp - n_alt},{n_alt}"
        )
    return "\n".join(head + body) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--ref",
        type=Path,
        required=True,
        help="indexed GRCh38 FASTA (UCSC names, chrM = rCRS)",
    )
    ap.add_argument("--tools", type=Path, help="folder with samtools, bgzip and tabix")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    samtools, bgzip, tabix = (
        tool(args.tools, n) for n in ("samtools", "bgzip", "tabix")
    )

    refs = {k: fetch(args.ref, args.tools, *w) for k, w in WINDOWS.items()}
    for k in WINDOWS:
        if "N" in refs[k] and k != "mt":  # rCRS keeps an N at 3107
            sys.exit(f"window {k} has Ns")

    # The viewer's reference: only the windows, in the igv-reports sliced
    # FASTA form igv.js reads without an index (>chr:start-end @len=N)
    with open(out / "GRCh38-demo.fa", "w") as fa:
        for k, (c, s, e) in WINDOWS.items():
            fa.write(f">{c}:{s}-{e} @len={LENGTHS[c]}\n")
            seq = refs[k]
            fa.writelines(seq[i : i + 80] + "\n" for i in range(0, len(seq), 80))

    with open(out / "genes.bed", "w") as bed:
        bed.writelines(f"{c}\t{s}\t{e}\t{n}\t0\t{st}\n" for c, s, e, n, st in GENES)

    for person, (sample, _, _, name) in PEOPLE.items():
        rng = random.Random(f"{SEED}{person}")
        fmt = name.split(".")[-1]
        if name.endswith(".vcf.gz"):
            path = out / name
            run(bgzip, "-f", "-o", str(path), input=vcf(person, refs).encode())
            run(tabix, "-f", "-p", "vcf", str(path))
            continue
        sam = header(sample) + "".join(
            line + "\n" for k in WINDOWS for line in reads(person, k, refs[k], rng)
        )
        target = out / name
        opts = (
            ["-O", "cram,no_ref=0,version=3.0", "--reference", str(args.ref)]
            if fmt == "cram"
            else ["-O", "bam,level=9"]
        )
        run(
            samtools,
            "sort",
            "-m",
            "512M",
            "--no-PG",
            *opts,
            "-o",
            str(target),
            "-",
            input=sam.encode(),
        )
        # CSI: a BAI's linear index grows with chromosome length, and these
        # windows sit far along real chromosomes
        run(samtools, "index", *([] if fmt == "cram" else ["-c"]), str(target))
    sizes = {p.name: p.stat().st_size for p in sorted(out.iterdir())}
    for name, size in sizes.items():
        print(f"{size / 1024:8.1f} KB  {name}")
    print(f"{sum(sizes.values()) / 1024:8.1f} KB  total")


if __name__ == "__main__":
    main()
