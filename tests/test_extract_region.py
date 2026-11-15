"""`extract region`: one region (chrM, chrY, both, or autosomes and X) as any
of a BAM, a VCF and a consensus FASTA, on a tiny BAM with planted SNPs."""

import logging
import random
from pathlib import Path

import pytest

from tests.htslib_helpers import (
    needs_htslib,
    run_wgsextract,
    sam_to_bam,
    vcf_calls,
    write_fasta,
)
from wgsextract_cli import api
from wgsextract_cli.core.utils import WGSExtractError, run_command

pytestmark = needs_htslib

CONTIGS = ("chr1", "chr7", "chrX", "chrY", "chrM")
SNP = 300  # 1-based; chr1 is heterozygous here, chrX/chrY/chrM all-ALT
STARTS = [201 + 8 * i for i in range(12)]  # every read covers SNP
IUPAC = {frozenset("AC"): "M", frozenset("AG"): "R", frozenset("AT"): "W"}
IUPAC |= {frozenset("CG"): "S", frozenset("CT"): "Y", frozenset("GT"): "K"}


def _alt(base: str) -> str:
    return {"A": "C", "C": "G", "G": "T", "T": "A"}[base]


def _with_alt(seq: str) -> str:
    return seq[: SNP - 1] + _alt(seq[SNP - 1]) + seq[SNP:]


@pytest.fixture(scope="module")
def genome(tmp_path_factory):
    """A reference (under an hg38 folder, so autosome calling gets GRCh38
    ploidy) and male and female BAMs; the male has no chr7 reads. Reads are named
    <contig>_<start>."""
    root = tmp_path_factory.mktemp("region")
    rng = random.Random(11)
    seqs = {c: "".join(rng.choice("ACGT") for _ in range(1000)) for c in CONTIGS}
    ref = write_fasta(root / "hg38" / "ref.fa", seqs)

    def reads(contig):
        alt = _with_alt(seqs[contig])
        return [
            f"{contig}_{s}\t0\t{contig}\t{s}\t60\t100M\t*\t0\t0\t"
            # chr1: every other read carries the ALT, a heterozygous SNP.
            f"{(alt if contig != 'chr1' or i % 2 else seqs[contig])[s - 1 : s + 99]}"
            f"\t{'I' * 100}"
            for i, s in enumerate(STARTS)
        ]

    header = [f"@SQ\tSN:{c}\tLN:1000" for c in CONTIGS]
    male = sam_to_bam(
        header + [r for c in CONTIGS if c != "chr7" for r in reads(c)],
        root / "male.bam",
    )
    female = sam_to_bam(
        header + [r for c in ("chr1", "chr7", "chrX", "chrM") for r in reads(c)],
        root / "female.bam",
    )
    run_wgsextract("sample", "set", "--input", male, "--sex", "Male")
    run_wgsextract("sample", "set", "--input", female, "--sex", "Female")
    return {"ref": str(ref), "seqs": seqs, "male": male, "female": female}


def _region(bam: str, outdir: Path, target: str, *extra: str) -> None:
    run_wgsextract(
        "extract", "region", "--input", bam, "--outdir", str(outdir),
        "--target", target, *extra,
    )  # fmt: skip


def _names(bam) -> set[str]:
    out = run_command(["samtools", "view", str(bam)], capture_output=True).stdout
    return {line.split("\t")[0] for line in out.splitlines()}


def _on(*contigs: str) -> set[str]:
    return {f"{c}_{s}" for c in contigs for s in STARTS}


def _fasta(path: Path) -> dict[str, str]:
    records: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line.startswith(">"):
            name = line[1:].split()[0]
            records[name] = ""
        else:
            records[name] += line
    return records


@pytest.mark.parametrize(
    ("target", "name", "contigs"),
    [
        ("mt", "male_mtDNA.bam", ["chrM"]),
        ("y", "male_Y.bam", ["chrY"]),
        ("ymt", "male_Y_MT.bam", ["chrY", "chrM"]),
        ("autox", "male_autosomes_X.bam", ["chr1", "chrX"]),
    ],
)
def test_bam_keeps_only_the_targets_reads(genome, tmp_path, target, name, contigs):
    _region(genome["male"], tmp_path, target, "--formats", "bam")
    assert _names(tmp_path / name) == _on(*contigs)
    assert sorted(p.name for p in tmp_path.iterdir()) == [name, f"{name}.bai"]


def test_ymt_vcf_and_fasta_carry_the_planted_snp_on_both_contigs(genome, tmp_path):
    _region(
        genome["male"],
        tmp_path,
        "ymt",
        "--formats",
        "vcf,fasta",
        "--ref",
        genome["ref"],
    )
    seqs = genome["seqs"]
    assert vcf_calls(tmp_path / "male_Y_MT.vcf.gz") == [
        (c, str(SNP), seqs[c][SNP - 1], _alt(seqs[c][SNP - 1]), "1")
        for c in ("chrY", "chrM")
    ]
    assert _fasta(tmp_path / "male_Y_MT.fasta") == {
        c: _with_alt(seqs[c]) for c in ("chrY", "chrM")
    }
    # The calls are made from the input; no temporary BAM is left.
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "male_Y_MT.fasta", "male_Y_MT.vcf.gz", "male_Y_MT.vcf.gz.tbi",
    ]  # fmt: skip


def test_fasta_alone_keeps_no_vcf(genome, tmp_path):
    _region(
        genome["male"], tmp_path, "mt", "--formats", "fasta", "--ref", genome["ref"]
    )
    assert _fasta(tmp_path / "male_MT.fasta") == {
        "chrM": _with_alt(genome["seqs"]["chrM"])
    }
    assert [p.name for p in tmp_path.iterdir()] == ["male_MT.fasta"]


def test_autox_calls_diploid_snps_and_writes_heterozygous_ones_as_iupac(
    genome, tmp_path
):
    _region(
        genome["male"],
        tmp_path,
        "autox",
        "--formats",
        "vcf,fasta",
        "--ref",
        genome["ref"],
    )
    seqs = genome["seqs"]
    calls = {c[0]: c for c in vcf_calls(tmp_path / "male_autosomes_X.vcf.gz")}
    assert sorted(calls) == ["chr1", "chrX"]  # never chrY or chrM
    assert calls["chr1"][4] in ("0/1", "1/0")
    assert calls["chrX"][4] == "1"  # a man's chrX is called haploid
    ref1 = seqs["chr1"][SNP - 1]
    het = IUPAC[frozenset((ref1, _alt(ref1)))]
    assert _fasta(tmp_path / "male_autosomes_X.fasta") == {
        "chr1": seqs["chr1"][: SNP - 1] + het + seqs["chr1"][SNP:],
        "chr7": seqs["chr7"],
        "chrX": _with_alt(seqs["chrX"]),
    }


def test_y_of_a_female_sample_warns_it_holds_no_y_dna(genome, tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        _region(genome["female"], tmp_path, "y", "--formats", "bam")
    assert _names(tmp_path / "female_Y.bam") == set()
    assert "appears female" in caplog.text
    assert "a female sample has none" in caplog.text


@pytest.mark.parametrize("formats", ["vcf", "fasta", "bam,vcf"])
def test_calling_requires_a_reference_and_writes_nothing_without_one(
    genome, tmp_path, formats
):
    with pytest.raises(WGSExtractError, match="--ref is required"):
        _region(genome["male"], tmp_path, "mt", "--formats", formats)
    assert list(tmp_path.iterdir()) == []


def test_failed_extraction_leaves_no_partial_output(genome, tmp_path):
    # Region queries need the index, so samtools fails after opening its output.
    unindexed = tmp_path / "in" / "male.bam"
    unindexed.parent.mkdir()
    unindexed.write_bytes(Path(genome["male"]).read_bytes())
    outdir = tmp_path / "out"
    with pytest.raises(WGSExtractError, match="Region extraction failed"):
        _region(
            str(unindexed),
            outdir,
            "ymt",
            "--formats",
            "bam,vcf",
            "--ref",
            genome["ref"],
        )
    assert list(outdir.iterdir()) == []


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"target": "mt"}, "one or more of fasta, bam, vcf"),
        ({"target": "mt", "formats": ""}, "one or more of fasta, bam, vcf"),
        ({"target": "mt", "formats": "bam,cram"}, "not cram"),
        ({"target": "ymt", "formats": "bam", "poz": True}, "--poz applies only"),
        ({"target": "y", "formats": "vcf", "poz": True}, "--poz applies only"),
        ({"target": "ymt", "formats": "vcf", "ybrowse": True}, "--ybrowse applies"),
        ({"target": "chr1", "formats": "bam"}, "mt, y, ymt, autox"),
    ],
)
def test_invalid_combinations_are_usage_errors(genome, tmp_path, fields, error):
    with pytest.raises(api.UsageError, match=error):
        api.ExtractRegionParams(input=genome["male"], outdir=tmp_path, **fields)
    assert list(tmp_path.iterdir()) == []


def test_api_reports_each_formats_role_and_the_variant_count(genome, tmp_path):
    result = api.extract.region(
        api.ExtractRegionParams(
            input=genome["male"],
            outdir=tmp_path,
            ref=genome["ref"],
            target="y",
            formats="bam,vcf,fasta",
        )
    )
    assert (result.status, result.exit_code) == ("ok", 0)
    assert {o.role: Path(o.path).name for o in result.outputs} == {
        "y_bam": "male_Y.bam",
        "y_vcf": "male_Y.vcf.gz",
        "y_fasta": "male_Y.fasta",
    }
    assert result.metrics["variants"] == 1
