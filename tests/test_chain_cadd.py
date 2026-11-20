"""Chain annotation forwards CADD's published resource options to its child."""

import pytest

from tests.htslib_helpers import needs_tools, reference_for_vcf, run_wgsextract
from tests.test_vcf_annotations import SAMPLE_ROWS, _query, _sample, _tsv

pytestmark = needs_tools("samtools", "bcftools", "bgzip", "tabix")


@pytest.mark.parametrize("with_indels", [False, True])
def test_1093_chain_cadd_applies_the_resolved_snv_and_indel_resources(
    tmp_path, with_indels
):
    library = tmp_path / "lib"
    resources = library / "downloads"
    resources.mkdir(parents=True)
    sample = _sample(tmp_path, SAMPLE_ROWS)
    header = "#Chrom\tPos\tRef\tAlt\tRawScore\tPHRED"
    _tsv(resources / "cadd_hg38.tsv.gz", header, ["1 100 C T 2.5 24.1"])
    if with_indels:
        _tsv(resources / "cadd_indel_hg38.tsv.gz", header, ["1 400 CA C 4.2 31"])
    reference_for_vcf(library / "genomes" / "hg38.fa.gz", sample)
    output = tmp_path / "out"
    run_wgsextract(
        "vcf", "chain-annotate", "--vcf-input", sample, "--ref", str(library),
        "--annotations", "cadd", "--outdir", str(output),
    )  # fmt: skip
    scores = dict(
        row.split(":", 1)
        for row in _query(output / "chain_annotated.vcf.gz", "%ID:%CADD_PHRED\n")
    )
    assert scores["snv"] == "24.1"
    assert scores["del"] == ("31" if with_indels else ".")
    assert not list(output.glob("chain_step_*"))
