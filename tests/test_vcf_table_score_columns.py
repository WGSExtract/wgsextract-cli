"""`vcf table`'s in-silico pathogenicity, splicing and conservation columns
(issue 1284), from VCFs annotated the way our annotators, Ensembl VEP and
fastVEP write them."""

import sqlite3
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, run_wgsextract
from tests.test_vcf_annotations import (
    AM_HEADER,
    HG38_CHR1,
    REVEL_HEADER,
    _sample,
    _tsv,
    _vcf,
)
from wgsextract_cli.core.variant_scores import SCORE_COLUMNS
from wgsextract_cli.core.variant_table import COLUMN_NAMES, parse_header, variant_rows

from .test_vcf_table import _record
from .test_vcf_table import _tsv as _table_tsv

SCORES = tuple(column.name for column in SCORE_COLUMNS)


def _rows(header: str, records: list[str], **kwargs) -> dict:
    rows = (
        dict(zip(COLUMN_NAMES, row, strict=True))
        for row in variant_rows(records, parse_header(header.splitlines()), **kwargs)
    )
    return {(str(row["pos"]), row["alt"]): row for row in rows}


def _scores(row: dict) -> dict:
    return {name: row[name] for name in SCORES if row[name] is not None}


# Ensembl VEP 113 with --everything and the REVEL, AlphaMissense, CADD,
# SpliceAI (split_output=1) and Conservation plugins.
VEP_CSQ = (
    "Allele|Consequence|IMPACT|SYMBOL|Feature_type|Feature|BIOTYPE|CANONICAL|"
    "MANE_SELECT|SIFT|PolyPhen|REVEL|am_class|am_pathogenicity|CADD_PHRED|CADD_RAW|"
    "SpliceAI_pred_DS_AG|SpliceAI_pred_DS_AL|SpliceAI_pred_DS_DG|"
    "SpliceAI_pred_DS_DL|SpliceAI_pred_SYMBOL|Conservation"
)
VEP_HEADER = f"""##fileformat=VCFv4.2
##contig=<ID=chr1,length=248956422>
##INFO=<ID=REVEL,Number=1,Type=Float,Description="REVEL score">
##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {VEP_CSQ}">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1
"""


def _csq(*entries: str) -> str:
    return "CSQ=" + ",".join(entries)


# A canonical transcript scored as damaging, and the MANE Select one (which
# the table picks) scored as benign. The CADD plugin scored only the first.
CANONICAL = (
    "G|missense_variant|MODERATE|GENEA|Transcript|ENST2|protein_coding|YES||"
    "deleterious(0.01)|probably_damaging(0.998)|0.91|likely_pathogenic|0.97|"
    "27.3|4.12|0.00|0.00|0.00|0.00|GENEA|"
)
MANE = (
    "G|missense_variant|MODERATE|GENEA|Transcript|ENST1|protein_coding||NM_1.1|"
    "tolerated(0.3)|benign(0.012)|0.12|likely_benign|0.08|||"
    "0.02|0.41|0.00|0.03|GENEA|5.2"
)


def test_1284_vep_scores_follow_the_chosen_transcript():
    record = _record("chr1", "100", ".", "A", "G", "50", "PASS",
                     _csq(CANONICAL, MANE), "GT", "0/1")  # fmt: skip
    row = _rows(VEP_HEADER, [record])["100", "G"]
    assert row["transcript_id"] == "ENST1"
    assert _scores(row) == {
        # The MANE Select transcript's transcript-level scores...
        "revel": 0.12,
        "alphamissense_score": 0.08,
        "alphamissense_class": "likely_benign",
        "sift_score": 0.3,
        "sift_prediction": "tolerated",
        "polyphen_score": 0.012,
        "polyphen_prediction": "benign",
        "spliceai_max": 0.41,
        "gerp": 5.2,
        # ...and the variant's CADD scores, from the entry that has them.
        "cadd_raw": 4.12,
        "cadd_phred": 27.3,
    }


def test_1284_our_annotators_info_field_comes_before_vep():
    # `vcf revel` after `vep run`: INFO/REVEL wins over the plugin's.
    record = _record("chr1", "100", ".", "A", "G", "50", "PASS",
                     "REVEL=0.55;" + _csq(MANE), "GT", "0/1")  # fmt: skip
    assert _rows(VEP_HEADER, [record])["100", "G"]["revel"] == 0.55


def test_1284_unscored_is_empty_never_zero():
    blank = "G|missense_variant|MODERATE|GENEA|Transcript|ENST1|protein_coding||NM_1.1"
    records = [
        _record("chr1", "100", ".", "A", "G", "50", "PASS", _csq(blank), "GT", "0/1"),
        # A 0 score is a real score, and stays.
        _record("chr1", "200", ".", "A", "G", "50", "PASS", "REVEL=0", "GT", "0/1"),
    ]
    rows = _rows(VEP_HEADER, records)
    assert _scores(rows["100", "G"]) == {}
    assert _scores(rows["200", "G"]) == {"revel": 0.0}
    unannotated = (
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )
    plain = _rows(unannotated, ["chr1\t5\t.\tA\tG\t.\tPASS\t.\n"], sample_index=None)
    assert _scores(plain["5", "G"]) == {}


# fastVEP 0.6 with --sa-dir: its VEP-like CSQ and its INFO projections.
FASTVEP_HEADER = """##fileformat=VCFv4.2
##contig=<ID=chr1,length=248956422>
##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from fastVEP. Format: Allele|Consequence|IMPACT|SYMBOL|Feature_type|Feature|MANE_SELECT|SIFT|PolyPhen">
##INFO=<ID=SpliceAI,Number=.,Type=String,Description="SpliceAI annotations. Format: ALLELE|SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|DP_AL|DP_DG|DP_DL">
##INFO=<ID=FV_REVEL,Number=.,Type=String,Description="fastVEP REVEL annotations. Format: ALLELE|SCORE">
##INFO=<ID=FV_ALPHAMISSENSE,Number=.,Type=String,Description="fastVEP AlphaMissense annotations. Format: ALLELE|PATHOGENICITY|CLASS">
##INFO=<ID=FV_PHYLOP,Number=.,Type=String,Description="fastVEP PhyloP annotations. Format: ALLELE|SCORE">
##INFO=<ID=FV_GERP,Number=.,Type=String,Description="fastVEP GERP annotations. Format: ALLELE|SCORE">
##INFO=<ID=FV_DBNSFP,Number=.,Type=String,Description="fastVEP dbNSFP annotations. Format: ALLELE|SIFT|POLYPHEN">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1
"""


def test_1284_fastvep_projections_give_each_alt_its_own_scores():
    info = ";".join([
        "CSQ=T|missense_variant|MODERATE|GENE%20A|Transcript|ENST1|NM_1.1||,"
        "G|missense_variant|MODERATE|GENE%20A|Transcript|ENST1|NM_1.1||",
        # SpliceAI scores two genes for G; the row's gene (GENE A, escaped as
        # fastVEP writes it) is the one reported. A missing delta is '.'.
        "SpliceAI=T|GENE%20A|0.01|0.00|0.00|0.00|1|2|3|4,"
        "G|GENE%20A|0.50|.|0.02|0.00|1|2|3|4,G|GENEB|0.90|0.00|0.00|0.00|1|2|3|4",
        "FV_REVEL=T|0.1,G|0.95",
        # Two entries for G (overlapping genes): the class agrees with the
        # highest score.
        "FV_ALPHAMISSENSE=G|0.12|likely_benign,G|0.97|likely_pathogenic",
        "FV_PHYLOP=T|2.5,G|2.5",
        "FV_GERP=T|4.1,G|4.1",
        "FV_DBNSFP=G|deleterious(0.010)|probably_damaging(0.980)",
    ])  # fmt: skip
    record = _record("chr1", "300", ".", "C", "T,G", "50", "PASS", info, "GT", "1/2")
    rows = _rows(FASTVEP_HEADER, [record])
    assert _scores(rows["300", "T"]) == {
        "revel": 0.1, "spliceai_max": 0.01, "phylop": 2.5, "gerp": 4.1,
    }  # fmt: skip
    assert _scores(rows["300", "G"]) == {
        "revel": 0.95,
        "alphamissense_score": 0.97,
        "alphamissense_class": "likely_pathogenic",
        "spliceai_max": 0.5,
        "phylop": 2.5,
        "gerp": 4.1,
        "sift_score": 0.01,
        "sift_prediction": "deleterious",
        "polyphen_score": 0.98,
        "polyphen_prediction": "probably_damaging",
    }


def test_1284_per_site_scores_of_a_multiallelic_record():
    """A Number=1 score can't belong to two ALTs, except conservation, which
    is the position's. A gVCF's <NON_REF> isn't a second ALT."""
    header = """##fileformat=VCFv4.2
##INFO=<ID=REVEL,Number=1,Type=Float,Description="REVEL score">
##INFO=<ID=PHYLOP,Number=1,Type=Float,Description="PhyloP conservation score">
##INFO=<ID=CADD_PHRED,Number=A,Type=Float,Description="CADD v1.7 PHRED-scaled score">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1
"""
    records = [
        _record("chr1", "100", ".", "C", "T,G", "50", "PASS",
                "REVEL=0.9;PHYLOP=3.1;CADD_PHRED=12,31", "GT", "1/2"),
        _record("chr1", "200", ".", "C", "T,<NON_REF>", "50", "PASS",
                "REVEL=0.7", "GT", "0/1"),
    ]  # fmt: skip
    rows = _rows(header, records)
    assert _scores(rows["100", "T"]) == {"phylop": 3.1, "cadd_phred": 12.0}
    assert _scores(rows["100", "G"]) == {"phylop": 3.1, "cadd_phred": 31.0}
    assert _scores(rows["200", "T"]) == {"revel": 0.7}


# --- end to end: our annotators, then vcf table ----------------------------

pytestmark_tools = needs_tools("bcftools", "bgzip", "tabix")


def _annotate(tmp_path: Path, sample: str) -> str:
    """Run each score annotator on the last one's output, from its resource
    files in the formats `ref` installs or the user supplies."""
    revel = _tsv(
        tmp_path / "revel.tsv.gz",
        REVEL_HEADER,
        ["1 100 100 C T 0.812", "1 200 200 A G 0.2", "1 200 200 A T 0.93"],
    )
    am = _tsv(
        tmp_path / "am.tsv.gz",
        AM_HEADER,
        [
            "chr1 100 C T hg38 P1 ENST1 A1V 0.9512 likely_pathogenic",
            "chr1 200 A T hg38 P1 ENST1 K2E 0.21 likely_benign",
        ],
    )
    cadd_header = "#Chrom\tPos\tRef\tAlt\tRawScore\tPHRED"
    cadd = _tsv(
        tmp_path / "cadd.tsv.gz",
        cadd_header,
        ["1 100 C T 2.5 24.1", "1 200 A G 0.1 3.2", "1 200 A T 3 30"],
    )
    cadd_indel = _tsv(
        tmp_path / "cadd_indel.tsv.gz", cadd_header, ["1 400 CA C 4.2 31"]
    )
    spliceai = _vcf(
        tmp_path / "spliceai.vcf.gz",
        [
            "100 . C T . . SpliceAI=T|G1|0.01|0.30|.|0.02|1|2|3|4 GT 0/1",
            "200 . A G . . SpliceAI=G|G1|0.00|0.00|0.00|0.00|1|2|3|4 GT 0/1",
            "200 . A T . . SpliceAI=T|G1|0.85|0.00|0.10|0.00|1|2|3|4,"
            "T|G2|0.95|0.00|0.00|0.00|1|2|3|4 GT 0/1",
        ],
        contig="1",
        info=("SpliceAI",),
    )
    phylop = _tsv(
        tmp_path / "phylop.tsv.gz",
        "#Chr\tStart\tEnd\tScore",
        ["1 100 100 3.25", "1 200 200 -1.5", "1 400 400 0.5"],
    )
    pyBigWig = pytest.importorskip("pyBigWig")
    gerp = str(tmp_path / "gerp.bw")
    bw = pyBigWig.open(gerp, "w")
    bw.addHeader([("1", HG38_CHR1)])
    bw.addEntries("1", 99, values=[2.5], span=1, step=1)
    bw.addEntries("1", 199, values=[-1.0], span=1, step=1)
    bw.addEntries("1", 399, values=[1.0, 3.0], span=1, step=1)
    bw.close()
    steps = [
        ("revel", "--revel-file", revel),
        ("alphamissense", "--am-file", am),
        ("cadd", "--cadd-file", cadd, "--cadd-indel-file", cadd_indel),
        ("spliceai", "--spliceai-file", spliceai),
        ("phylop", "--phylop-file", phylop),
        ("gerp", "--gerp-file", gerp),
    ]  # fmt: skip
    vcf = sample
    for command, *options in steps:
        outdir = tmp_path / command
        run_wgsextract(
            "vcf", command, "--input", vcf, *options, "--outdir", str(outdir)
        )
        vcf = str(outdir / f"{command}_annotated.vcf.gz")
    return vcf


@pytestmark_tools
def test_1284_annotator_scores_match_across_tsv_parquet_and_sqlite(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    sample = _sample(tmp_path, [
        "100 snv C T . PASS . GT 0/1",
        "200 multi A G,T . PASS . GT 1/2",
        "400 del CA C . PASS . GT 1/1",
        "500 plain G A . PASS . GT 0/1",
    ])  # fmt: skip
    annotated = _annotate(tmp_path, sample)
    out = tmp_path / "table"
    for fmt in ("tsv", "parquet"):
        run_wgsextract("vcf", "table", "--vcf-input", annotated, "--outdir", str(out),
                       "--format", fmt)  # fmt: skip

    tsv = {
        key: tuple(row[c] for c in SCORES)
        for key, row in _table_tsv(out / "gerp_annotated_variants.tsv").items()
    }
    empty = ("",) * 4
    assert tsv == {
        # revel, alphamissense score and class, cadd raw and phred,
        # spliceai_max, phylop, gerp, then SIFT and PolyPhen (VEP only).
        ("100", "T"): ("0.812", "0.9512", "likely_pathogenic", "2.5", "24.1",
                       "0.3", "3.25", "2.5", *empty),
        # Split by the first annotator: each ALT keeps its own scores, and
        # SpliceAI's highest gene for T (no gene to prefer without VEP).
        ("200", "G"): ("0.2", "", "", "0.1", "3.2", "0.0", "-1.5", "-1.0", *empty),
        ("200", "T"): ("0.93", "0.21", "likely_benign", "3.0", "30.0", "0.95",
                       "-1.5", "-1.0", *empty),
        # GERP's highest over the deletion's REF bases.
        ("400", "C"): ("", "", "", "4.2", "31.0", "", "0.5", "3.0", *empty),
        # Nothing scored it: every score column empty, never 0.
        ("500", "A"): ("",) * len(SCORES),
    }  # fmt: skip

    def text(value: object) -> str:
        return "" if value is None else str(value)

    table = pq.read_table(out / "gerp_annotated_variants.parquet")
    parquet = {
        (str(row["pos"]), row["alt"]): tuple(text(row[c]) for c in SCORES)
        for row in table.to_pylist()
    }
    db = sqlite3.connect(out / "gerp_annotated_variants.sqlite")
    db.row_factory = sqlite3.Row
    sqlite = {
        (str(row["pos"]), row["alt"]): tuple(text(row[c]) for c in SCORES)
        for row in db.execute("SELECT * FROM variants")
    }
    typed = db.execute(
        "SELECT typeof(revel), typeof(alphamissense_class), typeof(cadd_phred), "
        "typeof(spliceai_max) FROM variants WHERE pos = 100"
    ).fetchone()
    annotations = db.execute(
        "SELECT value FROM metadata WHERE key = 'annotations'"
    ).fetchone()[0]
    db.close()
    assert parquet == sqlite == tsv
    assert tuple(typed) == ("real", "text", "real", "real")
    assert set(annotations.split(",")) >= {
        "revel", "alphamissense", "cadd", "spliceai", "phylop", "gerp",
    }  # fmt: skip
