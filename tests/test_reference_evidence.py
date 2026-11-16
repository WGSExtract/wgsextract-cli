"""Reference evidence (#863): ``--reference-evidence gvcf`` and the classifier."""

import random
from pathlib import Path

import pytest

from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    run_wgsextract,
    sam_to_bam,
    write_fasta,
)
from wgsextract_cli import api
from wgsextract_cli.core.reference_evidence import (
    INDEX_ROLE,
    ROLE,
    ReferenceEvidence,
    Span,
    State,
)
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.core.variant_confidence import Thresholds

THRESHOLDS = Thresholds(min_depth=10, high_depth=20, min_gq=20, high_gq=30)


def _read(name: str, chrom: str, start: int, mapq: int, cigar: str, bases: str) -> str:
    return (
        f"{name}\t0\t{chrom}\t{start}\t{mapq}\t{cigar}\t*\t0\t0\t{bases}"
        f"\t{'I' * len(bases)}\tRG:Z:r"
    )


@pytest.fixture(scope="module")
def woman(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, str]:
    """chr20: 1-1000 at 30x with a homozygous 2 bp deletion after 700, no
    reads over 1001-1500, then 1501-2000 mostly at MAPQ 0 (30x) with 3x
    unique; chrY has reads but ploidy 0 for a woman."""
    root = tmp_path_factory.mktemp("reference evidence")
    rng = random.Random(863)
    seq = "".join(rng.choice("ACGT") for _ in range(3000))
    ref = write_fasta(root / "ref.fa", {"chr20": seq, "chrY": seq})
    reads = []
    for chrom in ("chr20", "chrY"):
        for i in range(300):
            start = 1 + i * 3
            bases, cigar = seq[start - 1 : start + 99], "100M"
            if start < 700 < start + 97:
                left = 700 - start + 1
                cigar = f"{left}M2D{100 - left}M"
                bases = seq[start - 1 : 700] + seq[702 : start + 101]
            reads.append((chrom, start, 60, cigar, bases))
        for i in range(330):
            start = 1501 + (i * 400) // 330
            reads.append((chrom, start, 0 if i % 10 else 60, "100M", seq[start - 1 : start + 99]))  # fmt: skip
    reads.sort(key=lambda read: (read[0] != "chr20", read[1]))
    lines = ["@HD\tVN:1.6\tSO:coordinate", "@SQ\tSN:chr20\tLN:3000"]
    lines += ["@SQ\tSN:chrY\tLN:3000", "@RG\tID:r\tSM:s"]
    lines += [_read(f"r{n}", *read) for n, read in enumerate(reads)]
    bam = sam_to_bam(lines, root / "woman.bam")
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Female")
    return bam, str(ref)


def _snp(sample: tuple[str, str], outdir: Path, evidence: str) -> api.Result:
    bam, ref = sample
    return api.vcf.snp(
        api.VcfSnpParams(
            input=Path(bam), ref=Path(ref), outdir=outdir, threads=2,
            ploidy="GRCh38", reference_evidence=evidence,
        )
    )  # fmt: skip


def _records(vcf: Path) -> list[str]:
    return run_command(["bcftools", "view", "-H", str(vcf)], capture_output=True).stdout.splitlines()  # fmt: skip


def _state_at(spans: list[Span], contig: str, position: int) -> State:
    return next(
        s.state for s in spans if s.contig == contig and s.start <= position <= s.end
    )


@needs_htslib
def test_reference_evidence_gap_keeps_callable_uncovered_and_failed_distinct(
    woman: tuple[str, str], tmp_path: Path
) -> None:
    """Issue #863: a variant-only VCF can't tell confident reference from a
    coverage gap or reads that map anywhere; the gVCF must keep them apart,
    and never claim the bases of an indel as reference."""
    result = _snp(woman, tmp_path / "gvcf", "gvcf")
    roles = {Path(o.path).name: o.role for o in result.outputs}
    assert roles["bcftools.g.vcf.gz"] == ROLE
    assert roles["bcftools.g.vcf.gz.tbi"] == INDEX_ROLE
    plain = _snp(woman, tmp_path / "plain", "none")
    assert all(o.role not in (ROLE, INDEX_ROLE) for o in plain.outputs)
    # The variant VCF is the same with or without the gVCF.
    assert _records(tmp_path / "gvcf" / "snps.vcf.gz") == _records(
        tmp_path / "plain" / "snps.vcf.gz"
    )

    spans = list(
        ReferenceEvidence(str(tmp_path / "gvcf" / "bcftools.g.vcf.gz")).spans(
            THRESHOLDS
        )
    )
    assert _state_at(spans, "chr20", 300) is State.CALLABLE_REFERENCE
    assert _state_at(spans, "chr20", 1200) is State.UNCOVERED
    # 30x deep, but only 3x at MAPQ 20 or more.
    assert _state_at(spans, "chr20", 1700) is State.LOW_QUALITY_REFERENCE
    assert {_state_at(spans, "chr20", p) for p in (700, 701, 702)} == {State.VARIANT}
    assert _state_at(spans, "chrY", 300) is State.EXCLUDED_BY_POLICY


_DV_HEADER = """##fileformat=VCFv4.2
##contig=<ID=chr1,length=300>
##contig=<ID=chrX,length=100>
##INFO=<ID=END,Number=1,Type=Integer,Description="End">
##FILTER=<ID=RefCall,Description="Reference call">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">
##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS
"""


@needs_htslib
def test_gq_gvcf_blocks_read_by_genotype_quality_and_minimum_depth(
    tmp_path: Path,
) -> None:
    """Issue #863: DeepVariant writes reads it never saw as 0/0 blocks with
    GQ 0 and MIN_DP 0, and a haploid reference call as 0, not as a no-call."""
    rows = [
        "chr1\t1\t.\tA\t<*>\t0\tPASS\tEND=100\tGT:GQ:MIN_DP\t0/0:50:30",
        "chr1\t101\t.\tA\t<*>\t0\tPASS\tEND=150\tGT:GQ:MIN_DP\t0/0:0:0",
        "chr1\t151\t.\tA\tG\t0\tRefCall\t.\tGT:GQ:DP\t0/0:15:25",
        "chr1\t152\t.\tA\t<*>\t0\tPASS\tEND=200\tGT:GQ:MIN_DP\t0/0:10:25",
        "chr1\t201\t.\tA\tC\t30\tPASS\t.\tGT:GQ:DP\t0/1:30:25",
        "chr1\t202\t.\tA\t<*>\t0\tPASS\tEND=250\tGT:GQ:MIN_DP\t./.:0:12",
        "chrX\t1\t.\tA\t<*>\t0\tPASS\tEND=100\tGT:GQ:MIN_DP\t0:40:6",
    ]
    plain = tmp_path / "dv.g.vcf"
    plain.write_text(_DV_HEADER + "\n".join(rows) + "\n")
    evidence = ReferenceEvidence(bgzip_index(plain))
    assert list(evidence.spans(THRESHOLDS)) == [
        Span("chr1", 1, 100, State.CALLABLE_REFERENCE),
        Span("chr1", 101, 150, State.UNCOVERED),
        Span("chr1", 151, 151, State.FILTERED),
        Span("chr1", 152, 200, State.LOW_QUALITY_REFERENCE),
        Span("chr1", 201, 201, State.VARIANT),
        Span("chr1", 202, 250, State.NO_CALL),
        Span("chr1", 251, 300, State.UNCOVERED),
        Span("chrX", 1, 100, State.CALLABLE_REFERENCE),
    ]
    # Bases outside the requested ones aren't reported.
    assert list(evidence.spans(THRESHOLDS, [("chr1", 95, 105)])) == [
        Span("chr1", 95, 100, State.CALLABLE_REFERENCE),
        Span("chr1", 101, 105, State.UNCOVERED),
    ]
