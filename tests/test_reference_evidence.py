"""Reference evidence (#863): ``--reference-evidence gvcf`` and the classifier."""

import json
import random
import sys
from pathlib import Path

import pytest

from tests.home_isolation import state_directory_setting
from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    run_wgsextract,
    sam_to_bam,
    write_fasta,
)
from tests.test_pharmcat import FAKE_PIPELINE
from wgsextract_cli import api
from wgsextract_cli.commands.vcf import pharmcat
from wgsextract_cli.core.reference_evidence import (
    INDEX_ROLE,
    ROLE,
    Provenance,
    ReferenceEvidence,
    Span,
    State,
)
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.core.variant_confidence import Thresholds
from wgsextract_cli.main import main

# The woman's chr20 SNP at 500 (her reference base there is never T).
SNP_ALT = "T"
THRESHOLDS = Thresholds(min_depth=10, high_depth=20, min_gq=20, high_gq=30)


def _read(name: str, chrom: str, start: int, mapq: int, cigar: str, bases: str) -> str:
    return (
        f"{name}\t0\t{chrom}\t{start}\t{mapq}\t{cigar}\t*\t0\t0\t{bases}"
        f"\t{'I' * len(bases)}\tRG:Z:r"
    )


@pytest.fixture(scope="module")
def woman(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, str]:
    """chr20: 1-1000 at 30x with a homozygous SNP at 500 and a homozygous
    2 bp deletion after 700, no reads over 1001-1500, then 1501-2000 mostly at MAPQ 0 (30x) with 3x
    unique; chrY has reads but ploidy 0 for a woman."""
    root = tmp_path_factory.mktemp("reference evidence")
    rng = random.Random(863)
    seq = "".join(rng.choice("ACGT") for _ in range(3000))
    seq = seq[:499] + "G" + seq[500:]
    ref = write_fasta(root / "ref.fa", {"chr20": seq, "chrY": seq})
    reads = []
    for chrom in ("chr20", "chrY"):
        for i in range(300):
            start = 1 + i * 3
            bases, cigar = seq[start - 1 : start + 99], "100M"
            if chrom == "chr20" and start <= 500 < start + 100:
                bases = bases[: 500 - start] + SNP_ALT + bases[501 - start :]
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
def test_issue_1044_real_gatk_block_loses_depth_evidence_when_min_dp_is_removed(
    tmp_path: Path,
) -> None:
    # First HaplotypeCaller block of public GATK NA12878 chr20 test data:
    # expected.CEUTrio.HiSeq.WGS.b37.NA12878.calls.20.10100000-10150000.g.vcf
    # SHA256 03c9560d6c38fe6f5b00a439657a5d05a832f50308fa7fa7fb258cf93fa79005.
    plain = tmp_path / "gatk.g.vcf"
    plain.write_text(
        _DV_HEADER.replace("chr1,length=300", "20,length=63025520")
        + "20\t10100000\t.\tC\t<NON_REF>\t.\t.\tEND=10100467"
        "\tGT:DP:GQ:MIN_DP\t0/0:68:99:56\n"
    )
    original = bgzip_index(plain)
    stripped = tmp_path / "without-minimum.g.vcf.gz"
    run_command(
        [
            "bcftools",
            "annotate",
            "-x",
            "FORMAT/MIN_DP",
            "-Oz",
            "-o",
            str(stripped),
            original,
        ]
    )
    run_command(["tabix", "-p", "vcf", str(stripped)])
    region = [("20", 10100000, 10100467)]
    assert list(ReferenceEvidence(original).spans(THRESHOLDS, region)) == [
        Span("20", 10100000, 10100467, State.CALLABLE_REFERENCE)
    ]
    assert list(ReferenceEvidence(str(stripped)).spans(THRESHOLDS, region)) == [
        Span("20", 10100000, 10100467, State.LOW_QUALITY_REFERENCE)
    ]


@needs_htslib
def test_issue_1044_clipped_single_base_block_does_not_inherit_start_dp(
    tmp_path: Path,
) -> None:
    from wgsextract_cli.commands.vcf.caller_ploidy import PloidyInterval
    from wgsextract_cli.commands.vcf.reference_evidence import clip_gvcf

    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "A" * 300})
    plain = tmp_path / "block.g.vcf"
    plain.write_text(
        _DV_HEADER
        + "chr1\t1\t.\tA\t<*>\t.\tPASS\tEND=100\tGT:DP:GQ\t0/0:30:60\n"
        + "chr1\t150\t.\tA\t.\t.\tPASS\t.\tGT:DP:GQ\t0/0:30:60\n"
    )
    source = bgzip_index(plain)
    clipped = tmp_path / "clipped.g.vcf.gz"
    clip_gvcf(source, [PloidyInterval("chr1", 49, 50, 2)], str(ref), str(clipped))
    assert list(
        ReferenceEvidence(str(clipped)).spans(THRESHOLDS, [("chr1", 50, 50)])
    ) == [Span("chr1", 50, 50, State.LOW_QUALITY_REFERENCE)]
    assert list(ReferenceEvidence(source).spans(THRESHOLDS, [("chr1", 150, 150)])) == [
        Span("chr1", 150, 150, State.CALLABLE_REFERENCE)
    ]


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
@pytest.mark.parametrize("engine", ["deepvariant", "clair3"])
def test_1103_native_diploid_encoded_haploid_blocks_use_policy_depth(tmp_path, engine):
    """#1103: native 0/0 blocks at 6x are callable off PAR, not inside PAR."""
    header = Provenance(
        engine,
        "wgse-deepvariant 0.4.0",
        "gq",
        ploidy="chrX:1-40=1,chrX:61-100=1",
    ).header()
    plain = tmp_path / "native.g.vcf"
    plain.write_text(
        _DV_HEADER.replace("#CHROM", header + "\n#CHROM")
        + "chrX\t1\t.\tA\t<*>\t0\tPASS\tEND=40\tGT:GQ:MIN_DP\t0/0:50:6\n"
        + "chrX\t41\t.\tA\t<*>\t0\tPASS\tEND=60\tGT:GQ:MIN_DP\t0/0:50:6\n"
        + "chrX\t61\t.\tA\t<*>\t0\tPASS\tEND=100\tGT:GQ:MIN_DP\t0/0:50:6\n"
    )
    assert list(
        ReferenceEvidence(bgzip_index(plain)).spans(THRESHOLDS, [("chrX", 1, 100)])
    ) == [
        Span("chrX", 1, 40, State.CALLABLE_REFERENCE),
        Span("chrX", 41, 60, State.LOW_QUALITY_REFERENCE),
        Span("chrX", 61, 100, State.CALLABLE_REFERENCE),
    ]


@needs_htslib
def test_1103_normalized_haploid_evidence_preserves_policy_and_quality(tmp_path):
    """#1103/#1026: alias normalization keeps haploid floors and GQ eligibility."""
    header = Provenance(
        "deepvariant", "0.4.0", "gq", ploidy="chrX:1-40=1,chrX:61-100=1"
    ).header()
    plain = tmp_path / "native.g.vcf"
    plain.write_text(
        _DV_HEADER.replace("#CHROM", header + "\n#CHROM")
        + "chrX\t1\t.\tA\t<*>\t0\tPASS\tEND=40\tGT:GQ:MIN_DP\t0/0:50:6\n"
        + "chrX\t41\t.\tA\t<*>\t0\tPASS\tEND=60\tGT:GQ:MIN_DP\t0/0:50:6\n"
        + "chrX\t61\t.\tA\t<*>\t0\tPASS\tEND=80\tGT:GQ:MIN_DP\t0/0:10:6\n"
        + "chrX\t81\t.\tA\t<*>\t0\tPASS\tEND=100\tGT:GQ:MIN_DP\t0/0:50:6\n"
    )
    reference = write_fasta(tmp_path / "ref.fa", {"X": "A" * 100})
    work = tmp_path / "normalized"
    work.mkdir()
    evidence = ReferenceEvidence(bgzip_index(plain)).normalized(reference, work)
    assert list(evidence.spans(THRESHOLDS, [("X", 1, 100)])) == [
        Span("X", 1, 40, State.CALLABLE_REFERENCE),
        Span("X", 41, 80, State.LOW_QUALITY_REFERENCE),
        Span("X", 81, 100, State.CALLABLE_REFERENCE),
    ]


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


@needs_htslib
@pytest.mark.parametrize("split", [False, True])
def test_clair3_deletion_shadow_keeps_overlapping_calls_1028(
    tmp_path: Path, split: bool
) -> None:
    """Issue #1028 review: another tool's Clair3 gVCF (``##source=Clair3``)
    gets the missed-deletion rule too, and a call overlapping a demoted
    block keeps its own state rather than reading as low quality."""
    rows = [
        "chr1\t10\t.\tA\t<*>\t0\tPASS\tEND=10\tGT:GQ:MIN_DP\t./.:0:40",
        f"chr1\t11\t.\tA\t<*>\t0\tPASS\tEND={16 if split else 30}\tGT:GQ:MIN_DP\t0/0:50:20",
        "chr1\t15\t.\tA\tC\t30\tPASS\t.\tGT:GQ:DP\t0/1:30:20",
        *(
            ["chr1\t17\t.\tA\t<*>\t0\tPASS\tEND=30\tGT:GQ:MIN_DP\t0/0:50:20"]
            if split
            else []
        ),
        "chr1\t31\t.\tA\t<*>\t0\tPASS\tEND=100\tGT:GQ:MIN_DP\t0/0:50:40",
    ]
    plain = tmp_path / "clair3.g.vcf"
    plain.write_text(
        _DV_HEADER.replace(
            "##fileformat=VCFv4.2\n", "##fileformat=VCFv4.2\n##source=Clair3\n"
        )
        + "\n".join(rows)
        + "\n"
    )
    evidence = ReferenceEvidence(bgzip_index(plain))
    assert list(evidence.spans(THRESHOLDS, [("chr1", 10, 40)])) == [
        Span("chr1", 10, 10, State.NO_CALL),
        Span("chr1", 11, 14, State.LOW_QUALITY_REFERENCE),
        Span("chr1", 15, 15, State.VARIANT),
        Span("chr1", 16, 20, State.LOW_QUALITY_REFERENCE),
        Span("chr1", 21, 40, State.CALLABLE_REFERENCE),
    ]


@needs_htslib
def test_clair3_long_anchor_chain_point_query_matches_full_region_1028(
    tmp_path: Path,
) -> None:
    rows = ["chr1\t100\t.\tA\t<*>\t0\tPASS\t.\tGT:GQ:DP\t./.:0:60"]
    rows += [
        f"chr1\t{pos}\t.\tA\t<*>\t0\tPASS\t.\tGT:GQ:DP\t./.:0:20"
        for pos in range(101, 131)
    ]
    rows += ["chr1\t131\t.\tA\t<*>\t0\tPASS\tEND=150\tGT:GQ:MIN_DP\t0/0:50:20"]
    plain = tmp_path / "chain.g.vcf"
    plain.write_text(
        _DV_HEADER.replace("##contig=", "##source=Clair3\n##contig=", 1)
        + "\n".join(rows)
        + "\n"
    )
    evidence = ReferenceEvidence(bgzip_index(plain))
    expected = [
        Span("chr1", 100, 130, State.NO_CALL),
        Span("chr1", 131, 140, State.LOW_QUALITY_REFERENCE),
        Span("chr1", 141, 150, State.CALLABLE_REFERENCE),
    ]
    assert list(evidence.spans(THRESHOLDS, [("chr1", 100, 150)])) == expected
    for pos in (131, 140, 141):
        assert list(evidence.spans(THRESHOLDS, [("chr1", pos, pos)])) == [
            Span("chr1", pos, pos, _state_at(expected, "chr1", pos))
        ]
    assert list(
        evidence.spans(
            THRESHOLDS, [("chr1", 60, 60), ("chr1", 131, 131), ("chr1", 260, 260)]
        )
    ) == [
        Span("chr1", 60, 60, State.UNCOVERED),
        Span("chr1", 131, 131, State.LOW_QUALITY_REFERENCE),
        Span("chr1", 260, 260, State.UNCOVERED),
    ]


@needs_htslib
@pytest.mark.parametrize("split", [False, True])
def test_clair3_anchor_demotes_preceding_reference_block_tail_1028(
    tmp_path: Path,
    split: bool,
) -> None:
    rows = [
        f"chr1\t1\t.\tA\t<*>\t0\tPASS\tEND={9 if split else 30}\tGT:GQ:MIN_DP\t0/0:50:20",
        "chr1\t10\t.\tA\t<*>\t0\tPASS\t.\tGT:GQ:DP\t./.:0:40",
        *(
            ["chr1\t11\t.\tA\t<*>\t0\tPASS\tEND=30\tGT:GQ:MIN_DP\t0/0:50:20"]
            if split
            else []
        ),
    ]
    plain = tmp_path / "overlap.g.vcf"
    plain.write_text(
        _DV_HEADER.replace("##contig=", "##source=Clair3\n##contig=", 1)
        + "\n".join(rows)
        + "\n"
    )
    evidence = ReferenceEvidence(bgzip_index(plain))
    assert list(evidence.spans(THRESHOLDS, [("chr1", 1, 30)])) == [
        Span("chr1", 1, 9, State.CALLABLE_REFERENCE),
        Span("chr1", 10, 10, State.NO_CALL),
        Span("chr1", 11, 20, State.LOW_QUALITY_REFERENCE),
        Span("chr1", 21, 30, State.CALLABLE_REFERENCE),
    ]


@needs_htslib
def test_clair3_separated_anchors_do_not_inherit_depth_1028(tmp_path: Path) -> None:
    plain = tmp_path / "separated.g.vcf"
    plain.write_text(
        _DV_HEADER.replace("##contig=", "##source=Clair3\n##contig=", 1)
        + "chr1\t100\t.\tA\t<*>\t0\tPASS\t.\tGT:GQ:DP\t./.:0:60\n"
        + "chr1\t101\t.\tA\t<*>\t0\tPASS\tEND=109\tGT:GQ:MIN_DP\t0/0:50:20\n"
        + "chr1\t110\t.\tA\t<*>\t0\tPASS\t.\tGT:GQ:DP\t./.:0:30\n"
        + "chr1\t111\t.\tA\t<*>\t0\tPASS\tEND=130\tGT:GQ:MIN_DP\t0/0:50:25\n"
    )
    evidence = ReferenceEvidence(bgzip_index(plain))
    assert list(evidence.spans(THRESHOLDS, [("chr1", 100, 130)])) == [
        Span("chr1", 100, 100, State.NO_CALL),
        Span("chr1", 101, 109, State.LOW_QUALITY_REFERENCE),
        Span("chr1", 110, 110, State.NO_CALL),
        Span("chr1", 111, 130, State.CALLABLE_REFERENCE),
    ]


@needs_htslib
def test_clair3_nested_anchor_preserves_furthest_end_1028(tmp_path: Path) -> None:
    plain = tmp_path / "nested.g.vcf"
    plain.write_text(
        _DV_HEADER.replace("##contig=", "##source=Clair3\n##contig=", 1)
        + "chr1\t10\t.\tAAAA\tA\t0\tRefCall\t.\tGT:GQ:DP\t0/1:0:60\n"
        + "chr1\t12\t.\tA\t<*>\t0\tPASS\t.\tGT:GQ:DP\t./.:0:20\n"
        + "chr1\t14\t.\tA\t<*>\t0\tPASS\tEND=30\tGT:GQ:MIN_DP\t0/0:50:25\n"
    )
    evidence = ReferenceEvidence(bgzip_index(plain))
    assert list(evidence.spans(THRESHOLDS, [("chr1", 10, 30)])) == [
        Span("chr1", 10, 13, State.FILTERED),
        Span("chr1", 14, 23, State.LOW_QUALITY_REFERENCE),
        Span("chr1", 24, 30, State.CALLABLE_REFERENCE),
    ]


@needs_htslib
@pytest.mark.parametrize("recovery", [False, True])
def test_clair3_deletion_shadow_stops_at_gap_or_depth_recovery_1028(
    tmp_path: Path, recovery: bool
) -> None:
    plain = tmp_path / "interrupted.g.vcf"
    plain.write_text(
        _DV_HEADER.replace("##contig=", "##source=Clair3\n##contig=", 1)
        + "chr1\t100\t.\tA\t<*>\t0\tPASS\t.\tGT:GQ:DP\t./.:0:60\n"
        + "chr1\t101\t.\tA\t<*>\t0\tPASS\tEND=103\tGT:GQ:MIN_DP\t0/0:50:20\n"
        + (
            "chr1\t104\t.\tA\t<*>\t0\tPASS\t.\tGT:GQ:DP\t0/0:50:42\n"
            if recovery
            else ""
        )
        + "chr1\t105\t.\tA\t<*>\t0\tPASS\tEND=110\tGT:GQ:MIN_DP\t0/0:50:20\n"
    )
    evidence = ReferenceEvidence(bgzip_index(plain))
    expected = [
        Span("chr1", 100, 100, State.NO_CALL),
        Span("chr1", 101, 103, State.LOW_QUALITY_REFERENCE),
    ]
    expected += (
        [Span("chr1", 104, 110, State.CALLABLE_REFERENCE)]
        if recovery
        else [
            Span("chr1", 104, 104, State.UNCOVERED),
            Span("chr1", 105, 110, State.CALLABLE_REFERENCE),
        ]
    )
    assert list(evidence.spans(THRESHOLDS, [("chr1", 100, 110)])) == expected
    assert list(evidence.spans(THRESHOLDS, [("chr1", 105, 105)])) == [
        Span("chr1", 105, 105, State.CALLABLE_REFERENCE)
    ]


@needs_htslib
@pytest.mark.parametrize(
    "engine", ["deepvariant", "gatk", "bcftools", "parabricks-deepvariant"]
)
def test_other_engines_do_not_inherit_clair3_deletion_rule_1028(
    tmp_path: Path,
    engine: str,
) -> None:
    from wgsextract_cli.core.reference_evidence import Provenance

    plain = tmp_path / "other.g.vcf"
    provenance = Provenance(engine, "test", "gq").header()
    plain.write_text(
        _DV_HEADER.replace("##contig=", f"{provenance}\n##contig=", 1)
        + "chr1\t10\t.\tA\t<*>\t0\tPASS\t.\tGT:GQ:DP\t./.:0:40\n"
        + "chr1\t11\t.\tA\t<*>\t0\tPASS\tEND=30\tGT:GQ:MIN_DP\t0/0:50:20\n"
    )
    assert list(
        ReferenceEvidence(bgzip_index(plain)).spans(THRESHOLDS, [("chr1", 10, 30)])
    ) == [
        Span("chr1", 10, 10, State.NO_CALL),
        Span("chr1", 11, 30, State.CALLABLE_REFERENCE),
    ]


def _seq(ref: str) -> str:
    return "".join(Path(ref).read_text().split(">chrY")[0].splitlines()[1:])


@needs_htslib
def test_863_pgs_counts_callable_reference_but_never_uncovered_sites(
    woman: tuple[str, str], tmp_path: Path
) -> None:
    """Issue #863: `score pgs` on a variant-only VCF reads the gVCF its caller
    published beside it, so a site the reads covered well is homozygous
    reference while a coverage gap or MAPQ-0 stretch stays missing."""
    _, ref = woman
    _snp(woman, tmp_path, "gvcf")
    seq = _seq(ref)
    scoring = tmp_path / "score.txt"
    rows = "".join(
        f"20\t{pos}\t{seq[pos - 1]}\t{'C' if seq[pos - 1] in 'AT' else 'A'}\t1\n"
        for pos in (300, 1200, 1700)
    )
    scoring.write_text(
        "#genome_build=GRCh38\n"
        "chr_name\tchr_position\teffect_allele\tother_allele\teffect_weight\n" + rows
    )

    def score() -> api.Result:
        return api.score.pgs(
            api.ScorePgsParams(
                input=tmp_path / "snps.vcf.gz", build="GRCh38", scoring_file=scoring,
                ref=Path(ref), outdir=tmp_path / "pgs",
            )
        )  # fmt: skip

    with_gvcf = score()
    assert with_gvcf.details["match_counts"] == {
        "callable_reference": 1,
        "uncovered": 1,
        "low_quality_reference": 1,
    }
    assert with_gvcf.metrics["score_sum"] == 2
    assert any(i.role == ROLE for i in with_gvcf.inputs)

    # Issue #863 review: a manifest without fingerprints can't vouch that its
    # gVCF belongs to this VCF, so it isn't used.
    manifests = list(tmp_path.glob("*.manifest.json"))
    for manifest in manifests:
        document = json.loads(manifest.read_text())
        for output in document["outputs"]:
            output.pop("fingerprint", None)
        manifest.write_text(json.dumps(document))
    assert manifests
    assert score().details["reference_evidence"] is None

    # Without the gVCF the same VCF scores as before: absent sites are missing.
    for name in ("bcftools.g.vcf.gz", "bcftools.g.vcf.gz.tbi"):
        (tmp_path / name).unlink()
    without = score()
    assert without.details["match_counts"] == {"missing": 3}
    assert without.details["reference_evidence"] is None


@needs_htslib
def test_863_pharmcat_fills_absent_sites_only_where_the_gvcf_read_reference(
    woman: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #863: PharmCAT definition sites a variant-only VCF doesn't list
    are reference only where the caller's gVCF read them well; a coverage gap
    or MAPQ-0 stretch is a no-call, so --absent-to-ref can't make it 0/0."""
    monkeypatch.setattr(pharmcat, "pixi_env_tool", lambda _tool: sys.executable)
    _, ref = woman
    _snp(woman, tmp_path, "gvcf")
    seq = _seq(ref)
    release = tmp_path / "pharmcat"
    release.mkdir()
    (release / "pharmcat_pipeline").write_text(FAKE_PIPELINE)
    (release / "pharmcat.jar").write_text("")
    (release / "pharmcat_regions.bed").write_text("chr20\t250\t1750\tPX=CYP2C19\n")
    (release / "pharmcat_positions.vcf").write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr20,length=3000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        + "".join(
            f"chr20\t{pos}\t.\t{seq[pos - 1]}\t{'C' if seq[pos - 1] != 'C' else 'G'}"
            "\t.\tPASS\t.\n"
            for pos in (300, 1200, 1700)
        )
    )
    plain = write_fasta(release / "reference.fna", {"chr20": seq})
    with open(release / pharmcat.REFERENCE_FASTA, "wb") as stream:
        run_command(["bgzip", "-c", str(plain)], stdout=stream)
    run_command(["samtools", "faidx", str(release / pharmcat.REFERENCE_FASTA)])

    def run() -> tuple[dict[int, str], dict[str, str]]:
        result = api.vcf.pharmcat(
            api.VcfPharmcatParams(
                vcf_input=tmp_path / "snps.vcf.gz", pharmcat_dir=release,
                outdir=tmp_path / "pgx", absent_to_ref=True,
            )
        )  # fmt: skip
        seen = {
            int(f[1]): f[9]
            for f in map(str.split, _records(release / "seen.vcf.gz"))
            if f[1] in ("300", "1200", "1700")
        }
        summary = next(
            c for c in result.details["confidence"] if c["role"] == "pgx_calls"
        )
        # The definition sites; the woman's own SNP at 500 is a call.
        states = {
            str(f["pos"]): f["PGX_STATE"]
            for f in summary["findings"]
            if f["pos"] in (300, 1200, 1700)
        }
        return seen, states

    seen, states = run()
    assert seen == {300: "0/0", 1200: "./.", 1700: "./."}
    assert (states["300"], states["1200"], states["1700"]) == (
        "reference", "uncovered", "low_quality_reference",
    )  # fmt: skip

    # Without the gVCF, as before: the sites are absent, left to PharmCAT.
    for name in ("bcftools.g.vcf.gz", "bcftools.g.vcf.gz.tbi"):
        (tmp_path / name).unlink()
    seen, _ = run()
    assert seen == {}


def _clinvar(path: Path, seq: str) -> Path:
    """A ClinVar VCF ("20", as ClinVar names it) over the woman's chr20: a
    pathogenic SNP on callable reference (300), a benign one (400), her SNP
    (500), a deletion she carries (700), and pathogenic SNPs in the gap
    (1200) and the MAPQ-0 stretch (1700)."""

    def snv(pos: int, alt: str = "") -> tuple[int, str, str]:
        ref = seq[pos - 1]
        return pos, ref, alt or ("C" if ref != "C" else "G")

    rows = [
        (*snv(300), "Pathogenic"),
        (*snv(400), "Benign"),
        (*snv(500, SNP_ALT), "Pathogenic"),
        (700, seq[699:702], seq[699], "Likely_pathogenic"),
        (*snv(1200), "Pathogenic"),
        (*snv(1700), "Pathogenic"),
    ]
    path.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=20,length=3000>\n"
        '##INFO=<ID=CLNSIG,Number=.,Type=String,Description="CLNSIG">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        + "".join(f"20\t{p}\t.\t{r}\t{a}\t.\t.\tCLNSIG={c}\n" for p, r, a, c in rows)
    )
    return path


# The gVCF's states at _clinvar's five pathogenic sites.
CLINVAR_SITE_STATES = {
    "sites": 5,
    "states": {
        "callable_reference": 1,
        "low_quality_reference": 1,
        "uncovered": 1,
        "variant": 2,
    },
}


def _evidence_of(result: api.Result, role: str) -> dict | None:
    summary = next(c for c in result.details["confidence"] if c["role"] == role)
    return summary.get("reference_evidence")


@needs_htslib
def test_863_clinvar_reports_the_gvcf_state_of_pathogenic_sites_not_carried(
    woman: tuple[str, str], tmp_path: Path
) -> None:
    """Issue #863: `vcf clinvar` on a variant-only VCF listed only the
    pathogenic calls, so a site it doesn't list read as "not carried" even
    where nobody looked. With the caller's gVCF beside the VCF it reports each
    ClinVar pathogenic site's state and warns about those neither called nor
    confident reference; without the gVCF the result is as before."""
    _, ref = woman
    _snp(woman, tmp_path, "gvcf")
    clinvar = _clinvar(tmp_path / "clinvar.vcf", _seq(ref))

    def run() -> api.Result:
        return api.vcf.clinvar(
            api.VcfClinvarParams(
                vcf_input=tmp_path / "snps.vcf.gz", clinvar_file=clinvar,
                ref=Path(ref), outdir=tmp_path / "clinvar", threads=1,
            )
        )  # fmt: skip

    result = run()
    evidence = _evidence_of(result, "clinvar_pathogenic")
    assert evidence is not None
    assert evidence["checked"]["clinvar_pathogenic_sites"] == CLINVAR_SITE_STATES
    assert evidence["checked"]["calls"] == {"sites": 1, "states": {"variant": 1}}
    warning = next(
        w for w in result.warnings if w.code == "reference_evidence_unassessed_sites"
    )
    assert (warning.details["count"], warning.details["total"]) == (2, 5)
    assert any(i.role == ROLE for i in result.inputs)

    for name in ("bcftools.g.vcf.gz", "bcftools.g.vcf.gz.tbi"):
        (tmp_path / name).unlink()
    without = run()
    assert _evidence_of(without, "clinvar_pathogenic") is None
    assert all(
        w.code != "reference_evidence_unassessed_sites" for w in without.warnings
    )


@needs_htslib
def test_863_gnomad_rare_calls_report_their_gvcf_state(
    woman: tuple[str, str], tmp_path: Path
) -> None:
    """Issue #863: `vcf gnomad --max-af` reports the gVCF's state at the rare
    calls it labelled, when the caller published one beside the VCF."""
    _, ref = woman
    _snp(woman, tmp_path, "gvcf")
    gnomad = tmp_path / "gnomad.vcf"
    gnomad.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr20,length=3000>\n"
        '##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )
    result = api.vcf.gnomad(
        api.VcfGnomadParams(
            vcf_input=tmp_path / "snps.vcf.gz", gnomad_file=gnomad, max_af=0.01,
            ref=Path(ref), outdir=tmp_path / "gnomad", threads=1,
        )
    )  # fmt: skip
    evidence = _evidence_of(result, "rare_variants")
    assert evidence is not None
    assert evidence["checked"] == {"calls": {"sites": 1, "states": {"variant": 1}}}


@needs_htslib
def test_863_analyze_reports_the_gvcf_state_of_clinvar_pathogenic_sites(
    woman: tuple[str, str], tmp_path: Path
) -> None:
    """Issue #863: `analyze` on a caller's VCF reports, with the gVCF beside
    it, the state of the ClinVar pathogenic sites its discovery filter looked
    for, not only of the significant calls it found."""
    _, ref = woman
    _snp(woman, tmp_path, "gvcf")
    clinvar = _clinvar(tmp_path / "clinvar.vcf", _seq(ref))
    config = tmp_path / "config.toml"
    config.write_text(
        f"clinvar_vcf_path = {json.dumps(str(clinvar))}\n"
        + state_directory_setting(tmp_path)
    )
    out = tmp_path / "out"
    args = [
        "--config", str(config), "analyze", "comprehensive", "--vcf-inputs",
        str(tmp_path / "snps.vcf.gz"), "--ref", ref, "--outdir", str(out),
        "--threads", "1",
    ]  # fmt: skip

    def run() -> dict:
        main(args)
        return next(
            json.loads(p.read_text())
            for p in out.glob("*.manifest.json")
            if "confidence" in (json.loads(p.read_text()).get("details") or {})
        )

    manifest = run()
    summary = next(
        c
        for c in manifest["details"]["confidence"]
        if c["role"] == "significant_variants"
    )
    checked = summary["reference_evidence"]["checked"]
    assert checked["clinvar_pathogenic_sites"] == CLINVAR_SITE_STATES
    assert checked["calls"] == {"sites": 1, "states": {"variant": 1}}
    assert "reference_evidence_unassessed_sites" in {
        w["code"] for w in manifest["warnings"]
    }
    # Issue #863 review: skipping discovery must not drop its confidence
    # details or coverage-gap warning from the next result manifest.
    cached = run()
    assert cached["details"]["confidence"] == manifest["details"]["confidence"]
    assert "reference_evidence_unassessed_sites" in {
        w["code"] for w in cached["warnings"]
    }
    assert any(i["role"] == ROLE for i in cached["inputs"])

    # Removing the companion must invalidate the cached evidence report.
    (tmp_path / "bcftools.g.vcf.gz.tbi").unlink()
    without = run()
    assert all("reference_evidence" not in c for c in without["details"]["confidence"])
    assert "reference_evidence_unassessed_sites" not in {
        w["code"] for w in without["warnings"]
    }
