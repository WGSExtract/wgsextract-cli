"""`vcf table`'s read evidence, phasing, transcript and gnomAD columns (issue
1289), from each caller's own FORMAT conventions, and the confidence labels'
reading of an AD that measures no reads."""

import sqlite3

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, run_wgsextract
from wgsextract_cli.core import variant_confidence
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.core.variant_table import COLUMN_NAMES, parse_header, variant_rows

from .test_vcf_table import RECORDS, _record, _rows, _tsv

# Issue 1289: read evidence, phasing and transcript columns. Each caller's own
# FORMAT conventions: GATK HaplotypeCaller (AD/DP, physical phasing with PGT,
# PID and PS), DeepVariant (AD and per-ALT VAF) and Clair3 (AD and a site
# AF), phased by WhatsHap (PS).
CALLER_FORMATS = """##fileformat=VCFv4.2
##contig=<ID=chr1,length=248956422>
##contig=<ID=chrX,length=156040895>
##INFO=<ID=DP,Number=1,Type=Integer,Description="Approximate read depth">
##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {csq}">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths for the ref and alt alleles in the order listed">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Approximate read depth (reads with MQ=255 or with bad mates are filtered)">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype Quality">
##FORMAT=<ID=PGT,Number=1,Type=String,Description="Physical phasing haplotype information">
##FORMAT=<ID=PID,Number=1,Type=String,Description="Physical phasing ID information">
##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phasing set (typically the position of the first variant in the set)">
##FORMAT=<ID=VAF,Number=A,Type=Float,Description="Variant allele fractions.">
##FORMAT=<ID=AF,Number=1,Type=Float,Description="Estimated allele frequency in the range of [0,1]">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1
"""
TX_CSQ = "Allele|Consequence|IMPACT|SYMBOL|Feature_type|Feature|HGVSc|MANE_SELECT"
GATK_RECORDS = [
    # Physically phased het: DP counts reads AD leaves out (uninformative).
    _record("chr1", "1000", ".", "A", "G", "500", "PASS",
            "DP=32;CSQ=G|missense_variant|MODERATE|GENEA|Transcript|"
            "ENST00000001.4|ENST00000001.4:c.10A>G|NM_1.2",
            "GT:AD:DP:GQ:PGT:PID:PS", "0|1:16,14:32:99:0|1:1000_A_G:1000"),
    # An unphased het in a phased VCF: its PS is not a phase block.
    _record("chr1", "2000", ".", "C", "T", "300", "PASS", "DP=20",
            "GT:AD:DP:GQ:PS", "0/1:10,10:20:99:1000"),
    # Multi-allelic: each ALT's reads over every read at the site.
    _record("chr1", "3000", ".", "T", "TA,TAA", "400", "PASS", "DP=25",
            "GT:AD:DP:GQ", "1/2:2,10,12:25:60"),
    # Covered by no reads at all.
    _record("chr1", "5000", ".", "G", "C", "30", "PASS", "DP=0",
            "GT:AD:DP:GQ", "0/1:0,0:0:3"),
    # A male's chrX call, haploid.
    _record("chrX", "4000", ".", "G", "A", "800", "PASS", "DP=25",
            "GT:AD:DP:GQ", "1:0,25:25:99"),
]  # fmt: skip


def _caller_rows(records: list[str], **kwargs) -> dict:
    header = parse_header(CALLER_FORMATS.format(csq=TX_CSQ).splitlines())
    rows = (
        dict(zip(COLUMN_NAMES, row, strict=True))
        for row in variant_rows(records, header, **kwargs)
    )
    return {(str(row["pos"]), row["alt"]): row for row in rows}


READ_COLUMNS = ("alt_fraction", "ad_ref", "ad_alt", "phase_set")


def test_1289_gatk_reads_and_phase_blocks():
    rows = _caller_rows(GATK_RECORDS)
    assert {key: tuple(row[c] for c in READ_COLUMNS) for key, row in rows.items()} == {
        # Informative reads only: AD's sum, not DP, is the denominator.
        ("1000", "G"): (14 / 30, 16, 14, "1000"),
        ("2000", "T"): (0.5, 10, 10, None),
        ("3000", "TA"): (10 / 24, 2, 10, None),
        ("3000", "TAA"): (12 / 24, 2, 12, None),
        ("4000", "A"): (1.0, 0, 25, None),
        # No reads: unknown, never a 0 fraction.
        ("5000", "C"): (None, 0, 0, None),
    }
    assert rows["4000", "A"]["zygosity"] == "hemizygous"


def test_1289_deepvariant_vaf_and_clair3_af_when_ad_is_missing():
    records = [
        # DeepVariant: AD first; its VAF only when AD is missing.
        _record("chr1", "100", ".", "A", "G", "40", "PASS", ".",
                "GT:GQ:DP:AD:VAF", "0/1:40:40:21,19:0.4"),
        _record("chr1", "200", ".", "A", "C,T", "40", "PASS", ".",
                "GT:GQ:DP:AD:VAF", "1/2:30:30:.:0.3,0.6"),
        # Clair3 phased by WhatsHap: AD, then its AF.
        _record("chr1", "300", ".", "C", "G", "20", "PASS", ".",
                "GT:GQ:DP:AD:AF:PS", "1|0:20:30:12,18:0.58:300"),
        _record("chr1", "400", ".", "C", "G", "20", "PASS", ".",
                "GT:GQ:DP:AF", "0/1:20:30:0.42"),
        # Genotype only: nothing measures the reads.
        _record("chr1", "500", ".", "C", "A", "20", "PASS", ".", "GT", "0|1"),
    ]  # fmt: skip
    rows = _caller_rows(records)
    assert {key: tuple(row[c] for c in READ_COLUMNS) for key, row in rows.items()} == {
        ("100", "G"): (19 / 40, 21, 19, None),
        ("200", "C"): (0.3, None, None, None),
        ("200", "T"): (0.6, None, None, None),
        ("300", "G"): (18 / 30, 12, 18, "300"),
        ("400", "G"): (0.42, None, None, None),
        ("500", "A"): (None, None, None, None),
    }


def test_1289_transcript_id_names_only_the_chosen_transcript():
    csq = "CSQ=G|regulatory_region_variant|MODIFIER||RegulatoryFeature|ENSR1||"
    records = [
        GATK_RECORDS[0],
        _record("chr1", "600", ".", "A", "G", "40", "PASS", csq, "GT", "0/1"),
    ]
    rows = _caller_rows(records)
    assert rows["1000", "G"]["transcript_id"] == "ENST00000001.4"
    assert rows["1000", "G"]["hgvs_c"] == "ENST00000001.4:c.10A>G"
    assert rows["600", "G"]["consequence"] == "regulatory_region_variant"
    assert rows["600", "G"]["transcript_id"] is None
    # SnpEff's Feature_ID, from the main fixture's ANN-only record.
    snpeff = _rows([RECORDS[4]])
    assert snpeff["500", "T"]["transcript_id"] == "NM_5"


def test_1289_unannotated_vcf_leaves_the_new_columns_empty(tmp_path):
    record = _record("chr1", "100", ".", "A", "G", "30", "PASS", ".", "GT", "0/1")
    row = _rows([record])["100", "G"]
    new = ("transcript_id", *READ_COLUMNS, "gnomad_popmax_af", "gnomad_nhomalt")
    assert [row[c] for c in new] == [None] * len(new)
    sites_only = _rows([record.rsplit("\t", 2)[0] + "\n"], sample_index=None)
    assert sites_only["100", "G"]["alt_fraction"] is None


GNOMAD_V4 = """##fileformat=VCFv4.2
##contig=<ID=chr1,length=248956422>
##INFO=<ID=AF,Number=A,Type=Float,Description="Alternate allele frequency">
##INFO=<ID=AF_grpmax,Number=A,Type=Float,Description="Maximum allele frequency across genetic ancestry groups">
##INFO=<ID=nhomalt,Number=A,Type=Integer,Description="Count of homozygous individuals">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
chr1\t1000\t.\tA\tG\t.\tPASS\tAF=0.01;AF_grpmax=0.08;nhomalt=12
chr1\t3000\t.\tT\tTA\t.\tPASS\tAF=0.2;AF_grpmax=0.35;nhomalt=900
chr1\t3000\t.\tT\tTAA\t.\tPASS\tAF=0.001;AF_grpmax=0.002;nhomalt=0
"""


@needs_htslib
def test_1289_new_columns_match_across_tsv_parquet_and_sqlite(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    plain = tmp_path / "gatk.vcf"
    plain.write_text(CALLER_FORMATS.format(csq=TX_CSQ) + "".join(GATK_RECORDS))
    gnomad = tmp_path / "gnomad.v4.vcf"
    gnomad.write_text(GNOMAD_V4)
    run_wgsextract(
        "vcf", "gnomad", "--vcf-input", bgzip_index(plain),
        "--gnomad-file", bgzip_index(gnomad), "--outdir", str(tmp_path),
    )  # fmt: skip
    annotated = str(tmp_path / "gnomad_annotated.vcf.gz")
    for fmt in ("tsv", "parquet"):
        run_wgsextract(
            "vcf", "table", "--vcf-input", annotated, "--outdir", str(tmp_path),
            "--format", fmt,
        )  # fmt: skip

    new = ("transcript_id", *READ_COLUMNS, "gnomad_popmax_af", "gnomad_nhomalt")
    tsv = {
        key: tuple(row[c] for c in new)
        for key, row in _tsv(tmp_path / "gnomad_annotated_variants.tsv").items()
    }
    assert tsv == {
        ("1000", "G"): (
            "ENST00000001.4",
            str(14 / 30),
            "16",
            "14",
            "1000",
            "0.08",
            "12",
        ),  # fmt: skip
        ("2000", "T"): ("", "0.5", "10", "10", "", "", ""),
        ("3000", "TA"): ("", str(10 / 24), "2", "10", "", "0.35", "900"),
        ("3000", "TAA"): ("", "0.5", "2", "12", "", "0.002", "0"),
        ("4000", "A"): ("", "1.0", "0", "25", "", "", ""),
        ("5000", "C"): ("", "", "0", "0", "", "", ""),
    }

    def text(value: object) -> str:
        return "" if value is None else str(value)

    table = pq.read_table(tmp_path / "gnomad_annotated_variants.parquet")
    parquet = {
        (str(row["pos"]), row["alt"]): tuple(text(row[c]) for c in new)
        for row in table.to_pylist()
    }
    db = sqlite3.connect(tmp_path / "gnomad_annotated_variants.sqlite")
    db.row_factory = sqlite3.Row
    sqlite = {
        (str(row["pos"]), row["alt"]): tuple(text(row[c]) for c in new)
        for row in db.execute("SELECT * FROM variants")
    }
    typed = db.execute(
        "SELECT typeof(alt_fraction), typeof(ad_alt), typeof(phase_set), "
        "typeof(gnomad_nhomalt) FROM variants WHERE pos = 1000"
    ).fetchone()
    db.close()
    assert parquet == sqlite == tsv
    assert tuple(typed) == ("real", "integer", "text", "integer")


@needs_htslib
def test_1289_a_gnomad_source_without_grpmax_drops_an_earlier_ones(tmp_path):
    plain = tmp_path / "gatk.vcf"
    plain.write_text(CALLER_FORMATS.format(csq=TX_CSQ) + GATK_RECORDS[0])
    gnomad = tmp_path / "gnomad.v4.vcf"
    gnomad.write_text(GNOMAD_V4)
    af_only = tmp_path / "gnomad.af.vcf"
    af_only.write_text(
        "\n".join(
            line
            for line in GNOMAD_V4.splitlines()
            if "AF_grpmax,Number" not in line and "nhomalt,Number" not in line
        ).replace(";AF_grpmax=0.08;nhomalt=12", "")
        + "\n"
    )
    first, second = tmp_path / "v4", tmp_path / "af"
    run_wgsextract(
        "vcf", "gnomad", "--vcf-input", bgzip_index(plain),
        "--gnomad-file", bgzip_index(gnomad), "--outdir", str(first),
    )  # fmt: skip
    run_wgsextract(
        "vcf", "gnomad", "--vcf-input", str(first / "gnomad_annotated.vcf.gz"),
        "--gnomad-file", bgzip_index(af_only), "--outdir", str(second),
    )  # fmt: skip
    values = run_command(
        ["bcftools", "query", "-f", "%GNOMAD_POPMAX_AF\t%GNOMAD_NHOMALT\n",
         str(first / "gnomad_annotated.vcf.gz")],
        capture_output=True,
    ).stdout  # fmt: skip
    assert values.strip() == "0.08\t12"
    header = run_command(
        ["bcftools", "view", "-h", str(second / "gnomad_annotated.vcf.gz")],
        capture_output=True,
    ).stdout
    defined = {tag for tag in ("GNOMAD_AF", "GNOMAD_POPMAX_AF", "GNOMAD_NHOMALT")
               if f"##INFO=<ID={tag}," in header}  # fmt: skip
    assert defined == {"GNOMAD_AF"}


@pytest.mark.parametrize(
    ("fmt", "sample", "fraction"),
    [
        # Review of issue 1289: no informative read is unknown, not 0.
        ("GT:AD:DP", "0/1:0,0:20", None),
        # A missing REF count without DP would read 12 of 12 reads as ALT.
        ("GT:AD", "0/1:.,12", None),
        ("GT:AD:AF", "0/1:.,12:0.55", 0.55),
        ("GT:AD:DP", "0/1:.,12:20", None),
    ],
)
def test_1289_unmeasured_reads_never_become_a_fraction(fmt, sample, fraction):
    record = _record("chr1", "900", ".", "C", "T", "30", "PASS", ".", fmt, sample)
    assert _caller_rows([record])["900", "T"]["alt_fraction"] == fraction


@pytest.mark.parametrize(
    ("fmt", "reason"),
    [
        # Review of issue 1289: AD 0,0 with a DP counts no informative read;
        # it read as fraction 0, a "low" allele_imbalance call.
        ({"GT": "0/1", "AD": "0,0", "DP": "40", "GQ": "99"}, "allele_balance_unknown"),
        ({"GT": "0/1", "AD": "0,0", "DP": "40", "GQ": "99", "VAF": "0.45"}, None),
        # A missing REF count without DP read 12 of 12 reads as ALT.
        ({"GT": "0/1", "AD": ".,12", "GQ": "99"}, "allele_balance_unknown"),
    ],
)
def test_1289_ad_without_reads_is_unknown_allele_balance(fmt, reason):
    label = variant_confidence.classify(
        alt=1, alleles=fmt["GT"].split("/"), ref="C", alt_allele="T",
        filters="PASS", info={}, fmt=fmt, n_alts=1,
        thresholds=variant_confidence.Thresholds(10, 20, 20, 30),
    )  # fmt: skip
    assert label is not None
    assert "allele_imbalance" not in label.reasons
    assert (
        (reason in label.reasons)
        if reason
        else ("allele_balance_unknown" not in label.reasons)
    )
