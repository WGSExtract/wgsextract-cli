import json

import pytest

from wgsextract_cli.commands.genotyping.common import Alignment
from wgsextract_cli.commands.genotyping.cyp2d6 import cyrius_genome, parse_cyrius
from wgsextract_cli.commands.genotyping.hla import parse_t1k, two_field
from wgsextract_cli.commands.genotyping.repeats import (
    THRESHOLDS,
    RepeatCall,
    classify,
    write_summary,
)
from wgsextract_cli.core.utils import WGSExtractError


def _alignment(build: str, names: list[str]) -> Alignment:
    return Alignment(
        path="s.cram",
        lengths=dict.fromkeys(names, 1),
        build=build,
        chr_prefix=names[0].startswith("chr"),
        reference="ref.fa",
        stem="s",
        outdir=".",
        threads="1",
    )


def test_alt_contigs_are_those_of_the_chromosome():
    aln = _alignment(
        "hg38",
        ["chr1", "chr22", "chr22_KI270879v1_alt", "chr2_KI270894v1_alt",
         "chr22_KI270731v1_random", "HLA-A*01:01:01:01"],
    )  # fmt: skip
    assert aln.alt_contigs("22") == ["chr22_KI270879v1_alt"]


def test_cyrius_genome_follows_build_and_contig_names():
    assert cyrius_genome(_alignment("hg38", ["chr1"])) == "38"
    assert cyrius_genome(_alignment("hg19", ["chr1"])) == "19"
    assert cyrius_genome(_alignment("hg19", ["1"])) == "37"
    with pytest.raises(WGSExtractError, match="chr1"):
        cyrius_genome(_alignment("hg38", ["1"]))


@pytest.mark.parametrize(
    ("genotype", "filter_", "call_info", "outside_call"),
    [
        ("*1/*2x2", "PASS", "unique_match", "*1/*2x2"),
        # Issue 844: a PASS pick among candidates, or Cyrius's LowQ_high_CN.
        ("*1/*4", "PASS", "pick_common_allele", None),
        ("*1/*4x5", "LowQ_high_CN", "unique_match", None),
        (
            "*1/*4;*4/*10",
            "More_than_one_possible_genotype",
            "more_than_one_match",
            None,
        ),
        ("None", "Not_assigned_to_haplotypes", "unique_match", None),
    ],  # fmt: skip
)
def test_only_settled_cyrius_calls_go_to_pharmcat(
    tmp_path, genotype, filter_, call_info, outside_call
):
    path = tmp_path / "s.cyrius.json"
    path.write_text(
        json.dumps(
            {"s": {"Genotype": genotype, "Filter": filter_, "Call_info": call_info,
                   "Total_CN": 3, "Total_CN_raw": 3.02, "Median_depth": 31.0}}
        )
    )  # fmt: skip
    assert parse_cyrius(str(path)).outside_call == outside_call


def test_t1k_calls_need_confident_alleles_that_agree_at_two_fields(tmp_path):
    path = tmp_path / "t1k_genotype.tsv"
    rows = [
        "HLA-A\t2\tHLA-A*32:01:01\t43.5\t23\tHLA-A*02:01:01\t42.5\t22\t",
        "HLA-B\t2\tHLA-B*44:02:01\t43.9\t26\tHLA-B*35:01:01\t40.1\t0\t",
        "HLA-C\t1\tHLA-C*05:01:01\t105.1\t60\t.\t0\t-1\t",
        "HLA-DRB4\t1\tHLA-DRB4*01:03:02,HLA-DRB4*01:124\t0.2\t5\t.\t0\t-1\t",
        "HLA-Y\t0\t.\t0\t-1\t.\t0\t-1\t",
    ]
    path.write_text("\n".join(rows) + "\n")
    calls = {call.gene: call for call in parse_t1k(str(path))}

    assert calls["HLA-A"].diplotype(two_field) == "*32:01/*02:01"
    assert calls["HLA-B"].diplotype(two_field) is None  # second allele quality 0
    assert calls["HLA-C"].diplotype(two_field) == "*05:01/*05:01"
    assert calls["HLA-DRB4"].diplotype(two_field) is None  # tied names differ
    assert calls["HLA-Y"].diplotype() is None


def test_two_field_keeps_the_expression_suffix():
    assert two_field("HLA-A*02:01:01:02") == "*02:01"
    assert two_field("HLA-A*01:01:01:02N") == "*01:01N"
    assert two_field("HLA-B*57:01") == "*57:01"


def _repeat(locus: str, *alleles: int, high: int | None = None) -> RepeatCall:
    return RepeatCall(
        variant_id=locus,
        repeat_unit="CAG",
        region="",
        alleles=alleles,
        intervals=tuple((a, high or a) for a in alleles),
        coverage=30.0,
    )


@pytest.mark.parametrize(
    ("call", "status"),
    [
        (_repeat("HTT", 17, 26), "normal"),
        (_repeat("HTT", 17, 30), "intermediate"),
        (_repeat("HTT", 17, 40), "pathogenic"),
        (_repeat("FXN", 9, 400), "carrier"),
        (_repeat("FXN", 300, 400), "pathogenic"),
        (_repeat("AR", 21, 45), "carrier"),  # X-linked recessive, two X
        (_repeat("AR", 45), "pathogenic"),  # one X
        (_repeat("FMR1", 30, 80), "premutation"),
        (_repeat("FMR1", 30, 250), "pathogenic"),
        (_repeat("RFC1", 9, 31), "normal"),
        (_repeat("RFC1", 9, 500), "carrier-length expansion, motif unchecked"),
        (_repeat("RFC1", 500, 600), "pathogenic-length expansion, motif unchecked"),
        (_repeat("HTT", 17, 20, high=40), "normal (CI reaches pathogenic)"),
        (_repeat("DIP2B", 7, 300), "-"),
    ],
)
def test_repeat_classification(call, status):
    assert classify(call, THRESHOLDS.get(call.variant_id)) == status


def _supported(locus, alleles, reads, coverage):
    return RepeatCall(locus, "CTG", "", alleles, None, coverage, allele_reads=reads)


@pytest.mark.parametrize(
    ("call", "status"),
    [
        # #643 review: LongTR's HG002 DMPK 11/19, the 19 from one read of 28.
        (_supported("DMPK", (11, 60), (27, 1), 28),
         "normal (unconfirmed: one read suggests an allele of 60, pathogenic)"),
        # Thin but true expansions of the PureTarget set (out/615/support.md).
        (_supported("DMPK", (12, 463), (245, 2), 247), "pathogenic"),
        (_supported("FXN", (304, 304), (1, 1), 1), "pathogenic"),
    ],
)  # fmt: skip
def test_low_support_alleles_alone_dont_make_a_repeat_pathogenic(
    tmp_path, call, status
):
    assert classify(call, THRESHOLDS[call.variant_id]) == status
    [(code, _locus, _line)] = write_summary(str(tmp_path / "s.tsv"), [call])
    assert code == (
        "repeat_unconfirmed" if "unconfirmed" in status
        else "repeat_outside_normal_range"
    )  # fmt: skip
