"""`microarray` on a VCF whose only record at some SNP markers is an indel, on
the tiny library of `test_microarray_simulation`."""

from pathlib import Path

import pytest

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
from wgsextract_cli.core.utils import run_command

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


def _targets(path: Path, rows: list[tuple[str, int, str]]) -> str:
    path.write_text(
        "#CHROM\tPOS\tID\n" + "".join(f"{c}\t{p}\t{r}\n" for c, p, r in rows)
    )
    with open(f"{path}.gz", "wb") as compressed:
        run_command(["bgzip", "-c", str(path)], stdout=compressed)
    run_command(["tabix", "-s", "1", "-b", "2", "-e", "2", f"{path}.gz"])
    return f"{path}.gz"


@pytest.mark.parametrize("filter_gate", ["--exclude-filtered", "--no-exclude-filtered"])
def test_issue_1249_indel_alone_does_not_make_chry_reference_filled(
    tmp_path, library, filter_gate
):
    fasta, _ = library
    # A female VCF's one chrY record: a mismapped indel at a marker.
    records = [*FEMALE_VCF[:3], "Y 1000 . A AT 50 PASS . GT 0/1"]
    vcf = _vcf(tmp_path / "female.vcf", ("1", "X", "Y", "MT"), records)
    targets = _targets(
        tmp_path / "targets.tab",
        [
            ("chr1", 1000, "rsHet"),
            ("chr1", 3000, "rsRef"),
            ("chrY", 1000, "rsY"),
            ("chrY", 2000, "rsY2"),
        ],
    )

    _microarray(
        vcf, fasta, tmp_path / "out", "--ref-vcf-tab", targets, "--assume-hom-ref",
        "--min-dp", "0", "--min-gq", "0", filter_gate, "--call-reasons",
    )  # fmt: skip

    report = _rows(tmp_path / "out/female_CombinedKit_reasons.tsv")
    assert report["rsRef"][-2:] == ["AA", "assumed_hom_ref"]
    assert report["rsY"][-2:] == ["--", "unsupported_allele"]
    assert report["rsY2"][-2:] == ["--", "absent_contig"]


@pytest.mark.parametrize(
    "gates",
    [
        ("--call-reasons",),
        ("--min-dp", "0", "--min-gq", "0", "--no-exclude-filtered"),
    ],
    ids=["default-gates", "no-gates"],
)
def test_issue_1249_haploid_indel_is_not_read_as_a_heterozygous_snp(
    tmp_path, library, gates
):
    fasta, _ = library
    # Haploid GTs have no separator: bcftools prints these as "AT" and "AC".
    records = [
        "1 1000 . A C 50 PASS . GT 0/1",
        "Y 1000 . A AT 50 PASS . GT 1",
        "MT 1000 . AC A 50 PASS . GT 0",
    ]
    vcf = _vcf(tmp_path / "male.vcf", ("1", "X", "Y", "MT"), records)

    _microarray(vcf, fasta, tmp_path / "out", *gates)

    kit = _rows(tmp_path / "out/male_CombinedKit.txt")
    assert (kit["rsY"][-1], kit["rsMT"][-1]) == ("--", "--")
    assert kit["rsHet"][-1] == "AC"
    if "--call-reasons" in gates:
        report = _rows(tmp_path / "out/male_CombinedKit_reasons.tsv")
        assert report["rsY"][-1] == report["rsMT"][-1] == "unsupported_allele"
