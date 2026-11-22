"""`microarray` on a VCF whose only record at some SNP markers is an indel, on
the tiny library of `test_microarray_simulation`."""

from tests.htslib_helpers import needs_htslib
from tests.test_microarray_simulation import (
    FEMALE_VCF,
    _genotypes,
    _microarray,
    _rows,
    _vcf,
)
from tests.test_microarray_simulation import (
    _synthetic_templates_only as _synthetic_templates_only,
)
from tests.test_microarray_simulation import library as library

pytestmark = needs_htslib


def test_issue_1249_indel_only_marker_is_a_no_call_not_assumed_reference(
    tmp_path, library
):
    fasta, _ = library
    records = [
        *FEMALE_VCF[:2],
        "1 3000 . A AT 50 PASS . GT 0/1",
        FEMALE_VCF[2],
        "1 4500 . AC A 50 PASS . GT 0/1",
        FEMALE_VCF[3],
    ]
    vcf = _vcf(tmp_path / "female.vcf", ("1", "X", "Y", "MT"), records)

    # Every gate off: the only mode that fills absent markers as reference.
    _microarray(
        vcf, fasta, tmp_path / "out", "--assume-hom-ref", "--min-dp", "0",
        "--min-gq", "0", "--no-exclude-filtered", "--call-reasons",
    )  # fmt: skip

    report = _rows(tmp_path / "out/female_CombinedKit_reasons.tsv")
    assert report["rsRef"][-2:] == ["--", "unsupported_allele"]
    assert report["rsNoReads"][-2:] == ["--", "unsupported_allele"]
    kit = _genotypes(tmp_path / "out" / "female_23andMe_V3.txt")
    assert (kit["rsRef"], kit["rsNoReads"]) == ("--", "--")
    assert (kit["rsHet"], kit["rsAlt"]) == ("AC", "CC")
