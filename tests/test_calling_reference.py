"""Wrong-reference regressions run against real FASTA/BAM/CRAM fixtures."""

import gzip
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, sam_to_bam, snp_sample, write_fasta
from wgsextract_cli import api
from wgsextract_cli.api.vcf import VcfSnpParams
from wgsextract_cli.core.calling_reference import validate_calling_reference
from wgsextract_cli.core.utils import WGSExtractError, run_command

pytestmark = needs_tools("samtools", "bcftools", "tabix")


@pytest.mark.parametrize("kind", ["bam", "cram"])
@pytest.mark.parametrize("sequences", [{"chr1": "A" * 2999}, {"1": "A" * 3000}])
def test_wrong_reference_851_fails_before_calling(tmp_path, kind, sequences):
    sample = snp_sample(tmp_path)
    wrong = write_fasta(tmp_path / "wrong.fa", sequences)
    output = tmp_path / "calls"
    result = api.vcf.snp(
        VcfSnpParams(
            input=Path(getattr(sample, kind)),
            ref=wrong,
            outdir=output,
            region="chr1:900-1100",
            ploidy="2",
            threads=1,
        ),
        check=False,
    )
    assert result.status == "failed"
    assert "Incompatible calling reference" in str(result.error)
    assert not (output / "snps.vcf.gz").exists()


def test_wrong_reference_851_accepts_alignment_subset(tmp_path):
    fasta = write_fasta(tmp_path / "ref.fa", {"chr1": "A" * 100, "chr2": "C" * 50})
    bam = sam_to_bam(["@SQ\tSN:chr1\tLN:100"], tmp_path / "subset.bam")
    validate_calling_reference(bam, str(fasta))


def test_wrong_reference_851_rejects_same_length_md5_when_available(tmp_path):
    sample = snp_sample(tmp_path)
    wrong = write_fasta(tmp_path / "wrong.fa", {"chr1": "A" * 3000})
    run_command(["samtools", "dict", "-o", str(tmp_path / "wrong.dict"), str(wrong)])
    with pytest.raises(WGSExtractError, match="sequence MD5 differs"):
        validate_calling_reference(sample.cram, str(wrong))


def test_wrong_reference_851_accepts_matching_ordinary_gzip(tmp_path):
    sample = snp_sample(tmp_path)
    compressed = tmp_path / "ordinary.fa.gz"
    compressed.write_bytes(gzip.compress(Path(sample.ref).read_bytes()))
    validate_calling_reference(sample.bam, str(compressed))
    assert not Path(str(compressed) + ".fai").exists()


def test_wrong_reference_851_gzip_md5_is_checked_without_dictionary_file(tmp_path):
    sample = snp_sample(tmp_path)
    wrong = tmp_path / "ordinary.fa.gz"
    wrong.write_bytes(gzip.compress(b">chr1\n" + b"A" * 3000 + b"\n"))
    with pytest.raises(WGSExtractError, match="sequence MD5 differs"):
        validate_calling_reference(sample.cram, str(wrong))


def test_wrong_reference_851_structural_subset_still_checks_shared_lengths(tmp_path):
    fasta = write_fasta(tmp_path / "ref.fa", {"chr1": "A" * 100})
    bam = sam_to_bam(
        ["@SQ\tSN:chr1\tLN:101", "@SQ\tSN:chrUn_decoy\tLN:50"], tmp_path / "subset.bam"
    )
    with pytest.raises(WGSExtractError, match="input length 101"):
        validate_calling_reference(bam, str(fasta), allow_reference_subset=True)
