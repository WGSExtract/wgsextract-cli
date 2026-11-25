"""Issue #1295: local allele agreement is not genotype or reference agreement."""

import csv
import gzip
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract
from wgsextract_cli import api
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib


def _vcf(path, records, *, chrom="chr1", length=80, metadata="", samples=("NA",)):
    header = (
        "##fileformat=VCFv4.2\n" + metadata + f"##contig=<ID={chrom},length={length}>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End">\n'
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|SYMBOL|ALLELE_NUM">\n'
        '##FILTER=<ID=LowQual,Description="Low quality">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t"
        + "\t".join(samples)
        + "\n"
    )
    path.write_text(
        header + "".join(f"{chrom}\t{row}\n" for row in records),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _rows(path):
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _inputs(a, b):
    return (f"a={a}", f"b={b}")


@pytest.fixture
def callsets(tmp_path):
    build = "##reference=GRCh38\n"
    a = _vcf(
        tmp_path / "caller-a.vcf",
        [
            "10\t.\tA\tG,T\t.\tPASS\tCSQ=G|missense_variant|GENE1|1,T|synonymous_variant|GENE2|2\tGT\t1/2\t0/0",
            "20\t.\tA\tG\t.\tPASS\t.\tGT\t0/1\t0/0",
            "30\t.\tA\tC\t.\tPASS\t.\tGT\t0/1\t0/0",
            "40\t.\tA\tC\t.\tPASS\t.\tGT\t0/1\t0/0",
            "50\t.\tA\tC\t.\tPASS\t.\tGT\t0/1\t0/0",
            "60\t.\tA\tC\t.\tPASS\t.\tGT\t0/1\t0/0",
            "70\t.\tA\tC\t.\tPASS\t.\tGT\t0/1\t0/0",
        ],
        metadata=build,
        samples=("NA", "OTHER"),
    )
    b = _vcf(
        tmp_path / "caller-b.vcf",
        [
            "10\t.\tA\tG\t.\tPASS\t.\tGT\t0|1",
            "10\t.\tA\tT\t.\tPASS\t.\tGT\t1/1",
            "20\t.\tA\tG\t.\tPASS\t.\tGT\t0/0",
            "30\t.\tA\t.\t.\tPASS\t.\tGT\t./.",
            "55\t.\tA\tG\t.\tPASS\t.\tGT\t1",
            "60\t.\tA\tG\t.\tPASS\t.\tGT\t0/1",
            "70\t.\tA\tC\t.\tLowQual\t.\tGT\t0/1",
        ],
        chrom="1",
        metadata=build,
    )
    gvcf = _vcf(
        tmp_path / "caller-b.g.vcf",
        [
            "25\t.\tA\t<NON_REF>\t.\tPASS\tEND=34\tGT:MIN_DP:GQ\t0/0:30:60",
            "35\t.\tA\t<NON_REF>\t.\tPASS\tEND=45\tGT:MIN_DP:GQ\t0/0:30:60",
            "47\t.\tA\t<NON_REF>\t.\tPASS\tEND=51\tGT:MIN_DP:GQ\t0/0:1:60",
        ],
        chrom="1",
        metadata=build + "##source=HaplotypeCaller\n",
    )
    return a, b, gvcf


def test_compare_1295_counts_carried_alt_and_genotype_separately(callsets, tmp_path):
    a, b, _ = callsets
    outdir = tmp_path / "comparison"
    run_wgsextract(
        "vcf",
        "compare",
        "--callset",
        f"a={a}",
        "--callset",
        f"b={b}",
        "--sample",
        "a=NA",
        "--emit-variants",
        "--emit-vcf",
        "--outdir",
        str(outdir),
    )
    summary = json.loads((outdir / "compare.summary.json").read_text(encoding="utf-8"))
    total = next(row for row in summary["pairs"] if row["variant_type"] == "all")
    assert (
        total["shared_alt"],
        total["a_only_observed"],
        total["b_only_observed"],
    ) == (2, 6, 2)
    assert (total["gt_compared"], total["gt_equal"], total["gt_concordance"]) == (
        2,
        1,
        0.5,
    )
    states = {
        r["pos"]: r["b_state"]
        for r in _rows(outdir / "compare.variants.tsv")
        if r["alt"] == "C"
    }
    assert states == {
        "30": "no_call",
        "40": "absent",
        "50": "absent",
        "60": "other_alt",
        "70": "filtered",
    }
    anchor = outdir / "compare.anchor.vcf.gz"
    assert (outdir / "compare.anchor.vcf.gz.csi").is_file()
    indexed = run_command(
        [
            "bcftools",
            "query",
            "-r",
            "chr1:10",
            "-f",
            "%ALT\t%WGSE_COMPARE_STATE\n",
            anchor,
        ],
        capture_output=True,
    ).stdout
    assert indexed.splitlines() == ["G\tcarried,carried", "T\tcarried,carried"]
    table = api.vcf.table(api.VcfTableParams(vcf_input=anchor, outdir=outdir))
    exported = _rows(
        Path(
            next(
                record.path
                for record in table.outputs
                if record.role == "variant_table"
            )
        )
    )
    assert {r["alt"]: r["gene"] for r in exported if r["pos"] == "10"} == {
        "G": "GENE1",
        "T": "GENE2",
    }


def test_compare_1295_strict_exclusion_requires_callable_gvcf(callsets, tmp_path):
    a, b, g = callsets
    strict = tmp_path / "strict"
    opts = api.VcfCompareParams(
        callset=_inputs(a, b),
        sample=("a=NA",),
        mode="unique",
        include=("a",),
        exclude=("b",),
        reference_evidence="gvcf",
        emit_vcf=True,
        outdir=strict,
    )
    result = api.vcf.compare(replace(opts, gvcf=(f"b={g}",)))
    rows = {row["pos"]: row for row in _rows(strict / "compare.variants.tsv")}
    assert [
        (rows[pos]["b_state"], rows[pos]["decision"])
        for pos in ("20", "30", "40", "50")
    ] == [
        ("uncovered", "inconclusive"),
        ("no_call", "inconclusive"),
        ("callable_reference", "retained"),
        ("low_quality_reference", "inconclusive"),
    ]
    manifest = json.loads(result.manifest_path().read_text(encoding="utf-8"))
    assert {r["role"] for r in manifest["inputs"] if r.get("fingerprint")} == {
        "callset:a",
        "callset:b",
        "gvcf:b",
    }
    assert manifest["metrics"]["retained"] == 1
    no_evidence = tmp_path / "no-evidence"
    result = api.vcf.compare(replace(opts, outdir=no_evidence))
    rows = {row["pos"]: row for row in _rows(no_evidence / "compare.variants.tsv")}
    assert rows["20"]["b_state"] == "not_called"
    assert rows["40"]["b_state"] == "not_assessed"
    assert rows["20"]["decision"] == rows["40"]["decision"] == "inconclusive"
    assert {"vcf_compare_no_reference_evidence"} <= {
        warning.code for warning in result.warnings
    }


def test_compare_1295_concordance_counts_callable_one_sided_alleles(callsets, tmp_path):
    a, b, gvcf = callsets
    api.vcf.compare(
        api.VcfCompareParams(
            callset=_inputs(a, b),
            sample=("a=NA",),
            reference_evidence="gvcf",
            gvcf=(f"b={gvcf}",),
            outdir=tmp_path,
        )
    )
    totals = next(
        row
        for row in _rows(tmp_path / "compare.summary.tsv")
        if row["variant_type"] == "all"
    )
    assert (totals["a_only_observed"], totals["a_only_callable"]) == ("6", "1")


def test_compare_1295_companion_cannot_mask_overlapping_primary_alt(tmp_path):
    build = "##reference=GRCh38\n"
    a = _vcf(
        tmp_path / "a.vcf",
        [
            "11\t.\tC\tG\t.\tPASS\t.\tGT\t0/1",
            "20\t.\tAC\tA\t.\tPASS\t.\tGT\t0/1",
            "30\t.\tA\tAT\t.\tPASS\t.\tGT\t0/1",
            "40\t.\tA\tG\t.\tPASS\t.\tGT\t0/1",
            "50\t.\tAC\tA\t.\tPASS\t.\tGT\t0/1",
            "60\t.\tA\tG\t.\tPASS\t.\tGT\t0/1",
            "70\t.\tA\tG\t.\tPASS\t.\tGT\t0/1",
        ],
        metadata=build,
    )
    b = _vcf(
        tmp_path / "b.vcf",
        [
            "10\t.\tAC\tA\t.\tPASS\t.\tGT\t0/1",
            "21\t.\tC\tG\t.\tPASS\t.\tGT\t0/1",
            "31\t.\tC\tG\t.\tLowQual\t.\tGT\t0/1",
            "51\t.\tC\t.\t.\tPASS\t.\tGT\t./.",
            "58\t.\tA\t<NON_REF>\t.\tPASS\tEND=62\tGT\t./.",
            "68\t.\tA\t.\t.\tLowQual\tEND=72\tGT\t0/0",
        ],
        chrom="1",
        metadata=build,
    )
    g = _vcf(
        tmp_path / "b.g.vcf",
        ["1\t.\tA\t<NON_REF>\t.\tPASS\tEND=80\tGT:MIN_DP:GQ\t0/0:30:60"],
        chrom="1",
        metadata=build + "##source=HaplotypeCaller\n",
    )
    outdir = tmp_path / "comparison"
    api.vcf.compare(
        api.VcfCompareParams(
            callset=_inputs(a, b),
            gvcf=(f"b={g}",),
            mode="unique",
            include=("a",),
            exclude=("b",),
            reference_evidence="gvcf",
            outdir=outdir,
        )
    )
    rows = _rows(outdir / "compare.variants.tsv")
    assert [
        (row["pos"], row["b_state"], row["decision"])
        for row in rows
        if row["a_state"] == "carried"
    ] == [
        ("11", "variant_evidence", "inconclusive"),
        ("20", "variant_evidence", "inconclusive"),
        ("30", "filtered", "inconclusive"),
        ("40", "callable_reference", "retained"),
        ("50", "no_call", "inconclusive"),
        ("60", "no_call", "inconclusive"),
        ("70", "filtered", "inconclusive"),
    ]


def test_compare_1295_unverified_override_does_not_bypass_known_mismatch(tmp_path):
    row = ["10\t.\tA\tC\t.\tPASS\t.\tGT\t0/1"]
    a = _vcf(tmp_path / "a.vcf", row)
    b = _vcf(tmp_path / "b.vcf", row)
    outdir = tmp_path / "comparison"
    result = api.vcf.compare(
        api.VcfCompareParams(callset=_inputs(a, b), outdir=outdir), check=False
    )
    assert result.exit_code == 2
    assert "unverified-build" in result.error.message
    assert not (outdir / "compare.summary.json").exists()
    result = api.vcf.compare(
        api.VcfCompareParams(
            callset=_inputs(a, b), outdir=outdir, allow_unverified_build=True
        )
    )
    assert result.details["allow_unverified_build"]
    assert "vcf_compare_unverified_build" in {
        warning.code for warning in result.warnings
    }
    _vcf(a, row, metadata="##reference=GRCh38\n")
    _vcf(b, row, metadata="##reference=GRCh37\n")
    for override in (False, True):
        result = api.vcf.compare(
            api.VcfCompareParams(
                callset=_inputs(a, b), outdir=outdir, allow_unverified_build=override
            ),
            check=False,
        )
        assert result.exit_code == 2
        assert "build mismatch" in result.error.message


def test_compare_1295_bed_is_applied_after_left_alignment(tmp_path):
    ref = tmp_path / "reference.fa"
    seq = "C" + "A" * 79
    ref.write_text(f">chr1\n{seq}\n", encoding="utf-8", newline="\n")
    run_command(["samtools", "faidx", ref])
    md5 = hashlib.md5(seq.encode()).hexdigest()
    a = _vcf(tmp_path / "shifted.vcf", ["7\t.\tAA\tA\t.\tPASS\t.\tGT\t0/1"])
    b = _vcf(tmp_path / "aligned.vcf", ["1\t.\tCA\tC\t.\tPASS\t.\tGT\t0/1"], chrom="1")
    for path in (a, b):
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "length=80>", f"length=80,md5={md5}>"
            ),
            encoding="utf-8",
            newline="\n",
        )
    bed = tmp_path / "panel.bed"
    bed.write_text("1\t0\t1\n", encoding="utf-8", newline="\n")
    outdir = tmp_path / "panel"
    api.vcf.compare(
        api.VcfCompareParams(
            callset=_inputs(a, b),
            mode="shared",
            include=("a", "b"),
            ref=ref,
            bed=bed,
            outdir=outdir,
            emit_vcf=True,
        )
    )
    assert [
        (r["chrom"], r["pos"], r["ref"], r["alt"], r["decision"])
        for r in _rows(outdir / "compare.variants.tsv")
    ] == [("chr1", "1", "CA", "C", "retained")]
    expected = _vcf(tmp_path / "expected.vcf", ["1\t.\tCA\tC\t.\tPASS\t.\tGT\t0/1"])
    oracle = tmp_path / "expected.vcf.gz"
    run_command(["bcftools", "view", "-Oz", "-o", oracle, expected])
    run_command(["bcftools", "index", oracle])
    intersection = run_command(
        # contract: exact REF and ALT identity, not just position or allele type.
        [
            "bcftools",
            "isec",
            "-c",
            "none",
            "-n",
            "=2",
            outdir / "compare.anchor.vcf.gz",
            oracle,
        ],
        capture_output=True,
    ).stdout
    assert intersection.splitlines() == ["chr1\t1\tCA\tC\t11"]


def test_compare_1295_truncated_bed_reports_invalid_scope(callsets, tmp_path):
    a, b, _ = callsets
    bed = tmp_path / "panel.bed.gz"
    bed.write_bytes(gzip.compress(b"chr1\t0\t10\n")[:-4])
    outdir = tmp_path / "comparison"
    result = api.vcf.compare(
        api.VcfCompareParams(
            callset=_inputs(a, b), sample=("a=NA",), bed=bed, outdir=outdir
        ),
        check=False,
    )
    assert result.exit_code == 2
    assert "Invalid comparison scope" in result.error.message
    assert not (outdir / "compare.summary.tsv").exists()


def test_compare_1295_nway_parquet_excludes_conflicting_duplicates_from_support(
    tmp_path,
):
    parquet = pytest.importorskip("pyarrow.parquet")
    build = "##reference=GRCh38\n"
    first = "10\t.\tA\tG\t.\tPASS\t.\tGT\t0/1"
    second = "20\t.\tA\tC\t.\tPASS\t.\tGT\t0/1"
    a = _vcf(
        tmp_path / "a.vcf", [first, first, second, second[:-3] + "1/1"], metadata=build
    )
    b = _vcf(tmp_path / "b.vcf", [first[:-3] + "1/.", second], metadata=build)
    c = _vcf(tmp_path / "c.vcf", [first[:-3] + "1"], metadata=build)
    outdir = tmp_path / "comparison"
    result = api.vcf.compare(
        api.VcfCompareParams(
            callset=(*_inputs(a, b), f"c={c}"),
            table_format="parquet",
            emit_variants=True,
            outdir=outdir,
        )
    )
    rows = parquet.read_table(outdir / "compare.variants.parquet").to_pylist()
    assert [(r["support"], r["a_state"], r["b_state"]) for r in rows] == [
        (3, "carried", "partial_alt"),
        (1, "conflicting_records", "carried"),
    ]
    pairs = parquet.read_table(outdir / "compare.summary.parquet").to_pylist()
    a_c = next(
        row
        for row in pairs
        if (row["a"], row["b"], row["variant_type"]) == ("a", "c", "all")
    )
    assert (a_c["shared_alt"], a_c["ploidy_mismatch"], a_c["gt_concordance"]) == (
        1,
        1,
        None,
    )
    assert "vcf_compare_conflicting_records" in {
        warning.code for warning in result.warnings
    }


def test_compare_1295_gvcf_scopes_normalized_insertion_with_adjacent_base(tmp_path):
    seq = "A" * 80
    ref = tmp_path / "ref.fa"
    ref.write_text(f">chr1\n{seq}\n", encoding="utf-8", newline="\n")
    run_command(["samtools", "faidx", ref])
    first = _vcf(tmp_path / "a.vcf", ["10\t.\tA\tAT\t.\tPASS\t.\tGT\t0/1"])
    second = _vcf(
        tmp_path / "b.g.vcf",
        [
            "1\t.\tA\t<NON_REF>\t.\tPASS\tEND=10\tGT:MIN_DP:GQ\t0/0:30:60",
            "11\t.\tA\t<NON_REF>\t.\tPASS\tEND=80\tGT:MIN_DP:GQ\t0/0:1:60",
        ],
        chrom="1",
    )
    outdir = tmp_path / "comparison"
    api.vcf.compare(
        api.VcfCompareParams(
            callset=_inputs(first, second),
            allow_unverified_build=True,
            ref=ref,
            mode="unique",
            include=("a",),
            exclude=("b",),
            reference_evidence="gvcf",
            region="1:10",
            outdir=outdir,
        )
    )
    rows = _rows(outdir / "compare.variants.tsv")
    assert [(r["pos"], r["b_state"], r["decision"]) for r in rows] == [
        ("10", "low_quality_reference", "inconclusive"),
    ]


def test_compare_1295_missing_gzi_does_not_modify_input_reference(tmp_path):
    fasta = tmp_path / "reference.fa"
    fasta.write_text(">chr1\n" + "A" * 80 + "\n", encoding="utf-8", newline="\n")
    ref = tmp_path / "reference.fasta.bgzf"
    with ref.open("wb") as compressed:
        run_command(["bgzip", "-c", fasta], stdout=compressed)
    run_command(["samtools", "faidx", ref])
    gzi = Path(f"{ref}.gzi")
    gzi.unlink()
    row = ["10\t.\tA\tG\t.\tPASS\t.\tGT\t0/1"]
    a = _vcf(tmp_path / "a.vcf", row)
    b = _vcf(tmp_path / "b.vcf", row)
    result = api.vcf.compare(
        api.VcfCompareParams(
            callset=_inputs(a, b),
            ref=ref,
            outdir=tmp_path / "comparison",
            allow_unverified_build=True,
        ),
        check=False,
    )
    assert result.exit_code == 2
    assert ".gzi" in result.error.message
    assert not gzi.exists()


def test_compare_1295_region_keeps_same_position_evidence_for_exclusions(tmp_path):
    build = "##reference=GRCh38\n"
    a = _vcf(tmp_path / "a.vcf", ["10\t.\tAC\tA\t.\tPASS\t.\tGT\t0/1"], metadata=build)
    b = _vcf(tmp_path / "b.vcf", ["10\t.\tA\tG\t.\tPASS\t.\tGT\t0/1"], metadata=build)
    outdir = tmp_path / "comparison"
    api.vcf.compare(
        api.VcfCompareParams(
            callset=_inputs(a, b),
            mode="unique",
            include=("a",),
            exclude=("b",),
            region="chr1:11",
            outdir=outdir,
        )
    )
    rows = _rows(outdir / "compare.variants.tsv")
    assert [(row["ref"], row["b_state"], row["decision"]) for row in rows] == [
        ("AC", "other_alt", "inconclusive"),
    ]


def test_compare_1295_output_does_not_replace_bed(tmp_path):
    build = "##reference=GRCh38\n"
    row = ["10\t.\tA\tG\t.\tPASS\t.\tGT\t0/1"]
    a = _vcf(tmp_path / "a.vcf", row, metadata=build)
    b = _vcf(tmp_path / "b.vcf", row, metadata=build)
    bed = tmp_path / "compare.summary.tsv"
    intervals = b"chr1\t9\t10\n"
    bed.write_bytes(intervals)
    result = api.vcf.compare(
        api.VcfCompareParams(callset=_inputs(a, b), bed=bed, outdir=tmp_path),
        check=False,
    )
    assert result.exit_code == 2
    assert "would replace an input" in result.error.message
    assert bed.read_bytes() == intervals


@pytest.mark.parametrize(
    ("ref", "is_conflict"), [("AC", False), ("NC", False), ("TC", True)]
)
def test_compare_1295_rejects_conflicting_bases_without_fasta(
    tmp_path, ref, is_conflict
):
    build = "##reference=GRCh38\n"
    a = _vcf(tmp_path / "a.vcf", ["10\t.\tA\tG\t.\tPASS\t.\tGT\t0/1"], metadata=build)
    b = _vcf(
        tmp_path / "b.vcf", [f"10\t.\t{ref}\tT\t.\tPASS\t.\tGT\t0/1"], metadata=build
    )
    outdir = tmp_path / "comparison"
    result = api.vcf.compare(
        api.VcfCompareParams(callset=_inputs(a, b), outdir=outdir), check=False
    )
    if is_conflict:
        assert result.exit_code == 2
        assert "Conflicting REF bases" in result.error.message
        assert "chr1:10" in result.error.message
        assert not (outdir / "compare.summary.json").exists()
    else:
        assert result.exit_code == 0
        assert result.metrics["alleles"] == 2
