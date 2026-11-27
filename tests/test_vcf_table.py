import contextlib
import csv
import json
import sqlite3
from pathlib import Path

import pytest

from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    reference_for_vcf,
    run_wgsextract,
)
from wgsextract_cli.core.csq_alleles import vep_allele
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.core.variant_table import COLUMN_NAMES, parse_header, variant_rows

CSQ = (
    "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|BIOTYPE|HGVSc|"
    "HGVSp|Existing_variation|CANONICAL|MANE_SELECT|gnomADg_AF|CLIN_SIG"
)
ANN = (
    "Allele | Annotation | Annotation_Impact | Gene_Name | Gene_ID | Feature_Type "
    "| Feature_ID | Transcript_BioType | Rank | HGVS.c | HGVS.p"
)
HEADER = f"""##fileformat=VCFv4.2
##contig=<ID=chr1,length=248956422>
##FILTER=<ID=LowQual,Description="Low quality">
##INFO=<ID=DP,Number=1,Type=Integer,Description="Depth">
##INFO=<ID=END,Number=1,Type=Integer,Description="End">
##INFO=<ID=CLNSIG,Number=.,Type=String,Description="ClinVar significance">
##INFO=<ID=CLNDN,Number=.,Type=String,Description="ClinVar disease name">
##INFO=<ID=GNOMAD_AF,Number=A,Type=Float,Description="gnomAD Allele Frequency">
##INFO=<ID=PHARMGKB,Number=.,Type=String,Description="ClinPGx (PharmGKB) clinical annotations: rsID|gene|level of evidence|phenotype category|drugs">
##INFO=<ID=WGSE_CONF,Number=A,Type=String,Description="Confidence">
##INFO=<ID=WGSE_CONF_REASONS,Number=A,Type=String,Description="Reasons">
##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {CSQ}">
##INFO=<ID=ANN,Number=.,Type=String,Description="Functional annotations: '{ANN}' ">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">
##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1
"""


def _record(*fields: str) -> str:
    return "\t".join(fields) + "\n"


RECORDS = [
    # Two transcripts: the MANE Select one is chosen over the canonical one.
    _record(
        "chr1",
        "100",
        "rs1",
        "A",
        "G",
        "50",
        "PASS",
        "DP=30;CLNSIG=Pathogenic;CLNDN=Some_disease;GNOMAD_AF=0.001;"
        "WGSE_CONF=high;WGSE_CONF_REASONS=moderate_depth|moderate_gq;CSQ="
        "G|intron_variant|MODIFIER|GENEA|ENSG1|Transcript|ENST1|protein_coding|"
        "ENST1.1:c.10+5A>G||rs1|YES|||,"
        "G|missense_variant|MODERATE|GENEA|ENSG1|Transcript|ENST2|protein_coding|"
        "ENST2.1:c.20A>G|ENSP2.1:p.Lys7%3D|rs1||NM_2.1||",
        "GT:DP:AD:GQ",
        "0/1:28:14,14:99",
    ),
    # A multi-allelic indel: VEP drops the shared first base, naming the
    # deletion "-" and the insertion "TT".
    _record(
        "chr1",
        "200",
        ".",
        "AT",
        "A,ATT",
        "40",
        "PASS",
        "GNOMAD_AF=0.1,0.2;CSQ="
        "-|frameshift_variant|HIGH|GENEB|ENSG2|Transcript|ENST3|protein_coding|"
        "ENST3.1:c.5del||rs2&COSV1|YES|||,"
        "TT|inframe_insertion|MODERATE|GENEB|ENSG2|Transcript|ENST3|protein_coding|"
        "ENST3.1:c.5dup||rs3|YES|||",
        "GT:AD:GQ",
        "1/2:0,9,8:40",
    ),
    # The sample carries only the first ALT.
    _record(
        "chr1",
        "300",
        ".",
        "C",
        "T,G",
        "60",
        "PASS",
        ".",
        "GT:DP:GQ",
        "0/1:20:50",
    ),
    # A gVCF reference block: no variant.
    _record(
        "chr1",
        "400",
        ".",
        "G",
        "<NON_REF>",
        ".",
        ".",
        "END=500",
        "GT:DP:GQ",
        "0/0:25:60",
    ),
    # SnpEff only; depth from AD; no QUAL.
    _record(
        "chr1",
        "500",
        "rs5;rs6",
        "C",
        "T",
        ".",
        "LowQual",
        "ANN=T|stop_gained|HIGH|GENEC|GENEC|transcript|NM_5|protein_coding|"
        "2/5|c.100C>T|p.Gln34*",
        "GT:AD",
        "1/1:0,12",
    ),
    _record(
        "chr1",
        "600",
        ".",
        "C",
        "T",
        "30",
        "PASS",
        "DP=22;PHARMGKB=rs7|CYP2C19|3|Toxicity|warfarin/aspirin,"
        "rs7|CYP2C19|1A|Metabolism/PK|clopidogrel",
        "GT",
        "0/1",
    ),
    _record("chr1", "700", ".", "G", "A", "30", "PASS", ".", "GT", "1"),
    # The spanning-deletion placeholder '*' gets no row.
    _record("chr1", "800", ".", "A", "*,T", "30", "PASS", ".", "GT", "1/2"),
]


@pytest.fixture
def annotated_vcf(tmp_path: Path) -> str:
    plain = tmp_path / "sample.vcf"
    plain.write_text(HEADER + "".join(RECORDS))
    return bgzip_index(plain)


def _tsv(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == COLUMN_NAMES
        return {(row["pos"], row["alt"]): row for row in reader}


@needs_htslib
def test_843_variant_table_keeps_ambiguous_pharmcat_alternatives(
    annotated_vcf, tmp_path
):
    phenotype = tmp_path / "ambiguous.phenotype.json"
    phenotype.write_text(
        json.dumps(
            {
                "geneReports": {
                    "CYP2C19": {
                        "recommendationDiplotypes": [
                            {
                                "allele1": {"name": "*1"},
                                "allele2": {"name": allele},
                                "phenotypes": [name],
                            }
                            for allele, name in (
                                ("*1", "Normal Metabolizer"),
                                ("*2", "Intermediate Metabolizer"),
                            )
                        ],
                    }
                }
            }
        )
    )
    out = tmp_path / "out"
    run_wgsextract(
        "vcf",
        "table",
        "--vcf-input",
        annotated_vcf,
        "--outdir",
        str(out),
        "--pharmcat-json",
        str(phenotype),
    )
    gene = _tsv(out / "sample_variants.tsv")["600", "T"]
    assert gene["pharmcat_diplotype"] == "Ambiguous: *1/*1 OR *1/*2"
    assert "no definitive guidance" in gene["pharmcat_phenotype"]
    assert "Normal Metabolizer" in gene["pharmcat_phenotype"]
    assert "Intermediate Metabolizer" in gene["pharmcat_phenotype"]


@needs_htslib
def test_vcf_table_flattens_annotations_one_row_per_carried_allele(
    annotated_vcf, tmp_path
):
    phenotype = tmp_path / "sample.phenotype.json"
    phenotype.write_text(
        json.dumps(
            {
                "geneReports": {
                    "CYP2C19": {
                        "recommendationDiplotypes": [
                            {
                                "allele1": {"name": "*1"},
                                "allele2": {"name": "*2"},
                                "phenotypes": ["Intermediate Metabolizer"],
                            }
                        ]
                    }
                }
            }
        )
    )
    out = tmp_path / "out"
    result = CommandResult("vcf table")
    with collecting(result):
        run_wgsextract(
            "vcf", "table", "--vcf-input", annotated_vcf, "--outdir", str(out),
            "--pharmcat-json", str(phenotype),
        )  # fmt: skip

    rows = _tsv(out / "sample_variants.tsv")
    assert sorted(rows) == [
        ("100", "G"), ("200", "A"), ("200", "ATT"), ("300", "T"),
        ("500", "T"), ("600", "T"), ("700", "A"), ("800", "T"),
    ]  # fmt: skip

    mane = rows["100", "G"]
    assert (mane["rsid"], mane["gene"], mane["consequence"], mane["impact"]) == (
        "rs1", "GENEA", "missense_variant", "MODERATE",
    )  # fmt: skip
    assert (mane["hgvs_c"], mane["hgvs_p"]) == ("ENST2.1:c.20A>G", "ENSP2.1:p.Lys7=")
    assert (mane["clinvar_significance"], mane["clinvar_condition"]) == (
        "Pathogenic", "Some_disease",
    )  # fmt: skip
    assert (mane["gnomad_af"], mane["confidence"]) == ("0.001", "high")
    assert mane["confidence_reasons"] == "moderate_depth|moderate_gq"
    assert (mane["genotype"], mane["zygosity"], mane["depth"], mane["gq"]) == (
        "0/1", "het", "28", "99",
    )  # fmt: skip
    assert (mane["qual"], mane["filter"]) == ("50.0", "PASS")

    deletion, insertion = rows["200", "A"], rows["200", "ATT"]
    assert (deletion["consequence"], deletion["gnomad_af"], deletion["rsid"]) == (
        "frameshift_variant", "0.1", "rs2",
    )  # fmt: skip
    assert (insertion["consequence"], insertion["gnomad_af"]) == (
        "inframe_insertion", "0.2",
    )  # fmt: skip
    assert deletion["zygosity"] == insertion["zygosity"] == "het"
    assert deletion["depth"] == "17"  # the sum of AD

    snpeff = rows["500", "T"]
    assert (snpeff["rsid"], snpeff["gene"], snpeff["consequence"]) == (
        "rs5;rs6", "GENEC", "stop_gained",
    )  # fmt: skip
    assert (snpeff["hgvs_c"], snpeff["hgvs_p"], snpeff["qual"]) == (
        "c.100C>T", "p.Gln34*", "",
    )  # fmt: skip
    assert (snpeff["zygosity"], snpeff["depth"], snpeff["filter"]) == (
        "hom_alt", "12", "LowQual",
    )  # fmt: skip

    pgx = rows["600", "T"]
    assert (pgx["rsid"], pgx["gene"], pgx["pharmgkb_level"], pgx["pharmgkb_drugs"]) == (
        "rs7", "CYP2C19", "1A", "aspirin; clopidogrel; warfarin",
    )  # fmt: skip
    assert (pgx["pharmcat_diplotype"], pgx["pharmcat_phenotype"]) == (
        "*1/*2", "Intermediate Metabolizer",
    )  # fmt: skip
    assert pgx["depth"] == "22"  # INFO/DP when the sample has none
    assert rows["700", "A"]["zygosity"] == "hemizygous"

    db = sqlite3.connect(out / "sample_variants.sqlite")
    assert db.execute("SELECT count(*) FROM variants").fetchone() == (8,)
    assert db.execute(
        "SELECT typeof(pos), typeof(gnomad_af), typeof(depth), typeof(qual) "
        "FROM variants WHERE pos = 100"
    ).fetchone() == ("integer", "real", "integer", "real")
    assert db.execute(
        "SELECT count(*) FROM variants WHERE gene = 'GENEB' AND gnomad_af < 0.15"
    ).fetchone() == (1,)
    indexes = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert {"variants_gene", "variants_rsid", "variants_position"} <= indexes
    plan = " ".join(
        str(row)
        for row in db.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM variants WHERE rsid = 'rs7'"
        )
    )
    assert "variants_rsid" in plan
    metadata = dict(db.execute("SELECT key, value FROM metadata"))
    assert metadata["sample"] == "S1"
    assert metadata["reference_build"] == "hg38"
    assert metadata["rows"] == "8"
    assert len(metadata["source_sha256"]) == 64
    assert metadata["annotations"] == "vep,snpeff,clinvar,gnomad,pharmgkb,confidence"
    described = [row[0] for row in db.execute("SELECT name FROM columns")]
    assert tuple(described) == COLUMN_NAMES
    db.close()

    roles = {Path(o.path).name: o.role for o in result.outputs}
    assert roles == {
        "sample_variants.tsv": "variant_table",
        "sample_variants.sqlite": "variant_database",
    }
    assert result.metrics["rows"] == 8
    assert metadata["population_af_sources"] == "gnomad"
    assert "gnomad_af_missing" not in {w.code for w in result.warnings}


@needs_htslib
def test_vcf_table_all_alleles_keeps_alleles_the_sample_lacks(annotated_vcf, tmp_path):
    run_wgsextract(
        "vcf", "table", "--vcf-input", annotated_vcf, "--outdir", str(tmp_path),
        "--all-alleles", "--no-sqlite", "--region", "chr1:300-400",
    )  # fmt: skip
    rows = _tsv(tmp_path / "sample_variants.tsv")
    assert {key: row["zygosity"] for key, row in rows.items()} == {
        ("300", "T"): "het",
        ("300", "G"): "not_carried",
    }
    assert not (tmp_path / "sample_variants.sqlite").exists()


def _csq(allele: str, hgvsc: str, allele_num: str = "") -> str:
    return (
        f"{allele}|inframe_insertion|MODERATE|GENEB|ENSG2|Transcript|ENST3|"
        f"protein_coding|{hgvsc}||||||{allele_num}"
    )


def _rows(records: list[str], csq: str = CSQ, **kwargs) -> dict:
    header = parse_header(
        HEADER.replace(f"Format: {CSQ}", f"Format: {csq}").splitlines()
    )
    rows = (
        dict(zip(COLUMN_NAMES, row, strict=True))
        for row in variant_rows(records, header, **kwargs)
    )
    return {(str(row["pos"]), row["alt"]): row for row in rows}


def test_split_records_take_only_their_own_alleles_csq():
    # `vcf clinvar` splits A>AAA,AA into two records that both keep every
    # allele's CSQ entries, with the original ALLELE_NUM.
    entries = ",".join([_csq("AA", "c.5_6dup", "1"), _csq("A", "c.6dup", "2")])
    records = [
        _record("chr1", "200", ".", "A", alt, "40", "PASS", f"CSQ={entries}",
                "GT", "0/1")
        for alt in ("AAA", "AA")
    ]  # fmt: skip
    rows = _rows(records, CSQ + "|ALLELE_NUM")
    assert rows[("200", "AAA")]["hgvs_c"] == "c.5_6dup"
    assert rows[("200", "AA")]["hgvs_c"] == "c.6dup"


@pytest.mark.parametrize(("ref", "alts"), [("A", ("C", "G")), ("a", ("c", "g"))])
def test_1630_split_record_matches_its_own_csq_without_allele_num(ref, alts):
    # A>C,G split by `vcf clinvar`; VEP had annotated only C, so the G record
    # must not inherit C's gene just because every entry names one allele.
    entries = _csq("C", "c.1A>C")
    records = [
        _record("chr1", "300", ".", ref, alt, "40", "PASS",
                f"CSQ={entries};OLD_MULTIALLELIC=chr1|300|A|C,G|{i}", "GT", "0/1")
        for i, alt in enumerate(alts, start=1)
    ]  # fmt: skip
    rows = _rows(records)
    assert rows[("300", alts[0])]["gene"] == "GENEB"
    assert rows[("300", alts[1])]["gene"] is None
    assert rows[("300", alts[1])]["hgvs_c"] is None


def test_split_record_uses_its_original_allele_num():
    # The G record is the first (only) ALT of its split record but ALT 2 of
    # the original, which VEP's ALLELE_NUM counts.
    entries = ",".join([_csq("C", "c.1A>C", "1"), _csq("G", "c.1A>G", "2")])
    records = [
        _record("chr1", "400", ".", "A", alt, "40", "PASS",
                f"CSQ={entries};OLD_MULTIALLELIC=chr1|400|A|C,G|{i}", "GT", "0/1")
        for i, alt in enumerate(("C", "G"), start=1)
    ]  # fmt: skip
    rows = _rows(records, CSQ + "|ALLELE_NUM")
    assert rows[("400", "C")]["hgvs_c"] == "c.1A>C"
    assert rows[("400", "G")]["hgvs_c"] == "c.1A>G"


@pytest.mark.parametrize(
    ("ref", "alts", "csq_allele"),
    [
        # VEP leaves spanning deletions out of the shared-first-base test.
        ("GT", "G,*", "-"),
        # <*> is an ordinary allele to VEP, not a structural variant.
        ("GT", "G,<*>", "-"),
        ("A", "AT,<*>", "T"),
    ],
)
def test_star_alleles_dont_stop_vep_trimming(ref, alts, csq_allele):
    record = _record("chr1", "500", ".", ref, alts, "40", "PASS",
                     f"CSQ={_csq(csq_allele, 'c.9del')}", "GT", "1/2")  # fmt: skip
    rows = _rows([record])
    assert list(rows) == [("500", alts.split(",")[0])]
    assert rows[("500", alts.split(",")[0])]["hgvs_c"] == "c.9del"


def test_vep_allele_follows_vep():
    assert vep_allele("GT", ["G", "*"], "*") == "*"
    assert vep_allele("A", ["C", "<*>"], "C") == "C"
    assert vep_allele("GT", ["*"], "*") == "*"
    assert vep_allele("A", ["<DEL>", "AT"], "AT") == "AT"


def test_vep_spelling_wins_over_raw_alt_without_allele_num():
    # VEP names A>AAA,AA "AA" and "A": the raw spelling of the second ALT
    # ("AA") must not also pick up the first ALT's entry.
    entries = ",".join([_csq("AA", "c.5_6dup"), _csq("A", "c.6dup")])
    record = _record("chr1", "200", ".", "A", "AAA,AA", "40", "PASS",
                     f"CSQ={entries}", "GT", "1/2")  # fmt: skip
    rows = _rows([record])
    assert rows[("200", "AAA")]["hgvs_c"] == "c.5_6dup"
    assert rows[("200", "AA")]["hgvs_c"] == "c.6dup"


@pytest.mark.parametrize("fmt, sample", [("GT", "."), ("DP", "12")])
def test_missing_genotype_is_a_no_call(fmt, sample):
    record = _record("chr1", "700", ".", "G", "A", "30", "PASS", ".", fmt, sample)
    assert _rows([record]) == {}
    row = _rows([record], carried_only=False)[("700", "A")]
    assert row["zygosity"] == "no_call"
    sites_only = _rows([record], sample_index=None)[("700", "A")]
    assert sites_only["zygosity"] is None


@needs_htslib
def test_vcf_table_region_reads_a_bcf_in_place(annotated_vcf, tmp_path):
    bcf = tmp_path / "sample.bcf"
    run_command(["bcftools", "view", "-Ob", "-o", str(bcf), annotated_vcf])
    run_wgsextract(
        "vcf", "table", "--vcf-input", str(bcf), "--outdir", str(tmp_path / "o"),
        "--no-sqlite", "--region", "chr1:300-400",
    )  # fmt: skip
    rows = _tsv(tmp_path / "o" / "sample_variants.tsv")
    assert list(rows) == [("300", "T")]
    assert (tmp_path / "sample.bcf.csi").exists()
    assert not (tmp_path / "sample.bcf.gz").exists()


@needs_htslib
def test_vcf_table_parquet_holds_the_tsv_rows(annotated_vcf, tmp_path, monkeypatch):
    pq = pytest.importorskip("pyarrow.parquet")
    from wgsextract_cli.commands.vcf import table as table_command

    hashed = []
    sha256 = table_command._sha256
    monkeypatch.setattr(
        table_command, "_sha256", lambda path: hashed.append(path) or sha256(path)
    )
    for fmt in ("tsv", "parquet"):
        run_wgsextract(
            "vcf", "table", "--vcf-input", annotated_vcf, "--outdir", str(tmp_path),
            "--no-sqlite", "--format", fmt,
        )  # fmt: skip
        # A TSV alone has nowhere to keep the digest, so the VCF isn't hashed.
        assert len(hashed) == (fmt == "parquet")
    table = pq.read_table(tmp_path / "sample_variants.parquet")
    assert tuple(table.column_names) == COLUMN_NAMES
    assert table.schema.field("pos").type == "int64"
    with open(tmp_path / "sample_variants.tsv", newline="") as f:
        text = list(csv.reader(f, delimiter="\t"))[1:]
    assert [
        ["" if v is None else str(v) for v in row.values()] for row in table.to_pylist()
    ] == text
    metadata = table.schema.metadata
    assert metadata[b"input"] == str(Path(annotated_vcf).absolute()).encode()
    assert b"sorted_by" in metadata
    assert len(metadata[b"source_sha256"]) == 64


@needs_htslib
def test_1289_phased_alleles_and_picked_transcripts_round_trip(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    csq_fields = CSQ + "|gnomADg_AF_grpmax|gnomADg_nhomalt|MAX_AF"

    def csq(**values):
        return "|".join(values.get(field, "") for field in csq_fields.split("|"))

    csq_g = csq(
        Allele="G", Consequence="intron_variant", Feature="ENST1", CANONICAL="YES",
        gnomADg_AF_grpmax="0.5", gnomADg_nhomalt="500",
    )  # fmt: skip
    csq_mane = csq(
        Allele="G", Consequence="missense_variant", Feature="ENST2", MANE_SELECT="NM_2",
        gnomADg_AF_grpmax="0.375", gnomADg_nhomalt="32",
    )  # fmt: skip
    csq_t = csq(
        Allele="T", Consequence="stop_gained", Feature="ENST3",
        gnomADg_AF_grpmax="0.25", gnomADg_nhomalt="12",
    )  # fmt: skip
    csq_reg = csq(
        Allele="T", Consequence="regulatory_region_variant", Feature="ENSR1",
        Feature_type="RegulatoryFeature", MAX_AF="0.9",
    )  # fmt: skip
    header = HEADER.replace(CSQ, csq_fields).replace(
        "##FORMAT=<ID=GT,",
        '##INFO=<ID=GNOMAD_POPMAX_AF,Type=Float,Description="Group max AF",Number=A>\n'
        '##INFO=<ID=GNOMAD_NHOMALT,Type=Integer,Number=A,Description="Homozygotes">\n'
        '##INFO=<ID=AF,Number=A,Type=Float,Description="Cohort AF">\n'
        '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">\n'
        '##FORMAT=<ID=VAF,Number=A,Type=Float,Description="Variant fraction">\n'
        '##FORMAT=<ID=AF,Type=Float,Description="Fraction, including REF",Number=R>\n'
        "##FORMAT=<ID=GT,",
    )
    sample = tmp_path / "sample.vcf"
    sample.write_text(
        header + "chr1\t100\t.\tA\tG,T\t.\tPASS\t"
        "GNOMAD_POPMAX_AF=0.125,.;GNOMAD_NHOMALT=0,.;"
        f"CSQ={csq_g},{csq_mane},{csq_t}\tGT:AD:PS:VAF\t1|2:10,2,8:100:0.4,0.4\n"
        "chr1\t200\t.\tC\tG,T\t.\tPASS\t"
        "AF=0.99,0.99;ANN=G|stop_gained|HIGH|GENEA||transcript|NM_4"
        "\tGT:AD:PS:VAF:AF\t1/2:.,2,8:100:0.25,.:0.125,0.375,0.5\n"
        "chr1\t300\t.\tA\tT\t.\tPASS\t"
        f"CSQ={csq_reg}\tGT:AD:PS:AF\t0|1:10,0:.:0.5,0.5\n"
        "chr1\t400\t.\tA\tT\t.\tPASS\tAF=0.25\tGT:AD\t0/1:0,0\n",
        newline="\n",
    )
    args = ("vcf", "table", "--vcf-input", bgzip_index(sample))
    for fmt in ("tsv", "parquet"):
        run_wgsextract(*args, "--outdir", str(tmp_path / fmt), "--format", fmt)
    columns = "pos alt phase_set alt_fraction transcript_id gnomad_popmax_af gnomad_nhomalt".split()
    expected = [
        (100, "G", "100", 0.1, "ENST2", 0.125, 0),
        (100, "T", "100", 0.4, "ENST3", 0.25, 12),
        (200, "G", None, 0.25, "NM_4", None, None),
        (200, "T", None, 0.5, None, None, None),
        (300, "T", None, 0.0, None, None, None),
        (400, "T", None, None, None, None, None),
    ]
    with contextlib.closing(
        sqlite3.connect(tmp_path / "tsv" / "sample_variants.sqlite")
    ) as db:
        assert (
            db.execute(f"SELECT {', '.join(columns)} FROM variants").fetchall()
            == expected
        )
    parquet = pq.read_table(
        tmp_path / "parquet" / "sample_variants.parquet", columns=columns
    )
    assert [tuple(row.values()) for row in parquet.to_pylist()] == expected
    with (tmp_path / "tsv" / "sample_variants.tsv").open(newline="") as f:
        assert [
            tuple(row[column] for column in columns)
            for row in csv.DictReader(f, delimiter="\t")
        ] == [tuple("" if v is None else str(v) for v in row) for row in expected]


@needs_htslib
@pytest.mark.parametrize("with_reference", [False, True])
def test_1289_split_annotation_preserves_original_allele_fraction(
    tmp_path, with_reference
):
    plain = tmp_path / "sample.vcf"
    header = HEADER.replace("248956422", "1000").replace(
        "##FORMAT=<ID=GT,",
        '##FORMAT=<ID=VAF,Number=A,Type=Float,Description="Variant fraction">\n'
        "##FORMAT=<ID=GT,",
    )
    plain.write_text(
        header
        + "chr1\t100\t.\tC\tG,T\t.\tPASS\t.\tGT:AD:VAF\t1|2:10,2,8:.,.\n"
        + "chr1\t200\t.\tC\tG,T\t.\tPASS\t.\tGT:AD:VAF\t1|2:10,2,.:0.05,0.4\n",
        newline="\n",
    )
    sample = bgzip_index(plain)
    resource = tmp_path / "gnomad.tsv"
    resource.write_text("chr1\t100\tC\tG\t0.05\n", newline="\n")
    outdir = tmp_path / "out"
    ref_args = ()
    if with_reference:
        reference = reference_for_vcf(tmp_path / "ref.fa.gz", sample)
        ref_args = ("--ref", str(reference))
    run_wgsextract(
        "vcf", "gnomad", "--vcf-input", sample, "--gnomad-file", str(resource),
        "--outdir", str(outdir), *ref_args,
    )  # fmt: skip
    run_wgsextract(
        "vcf", "table", "--vcf-input", str(outdir / "gnomad_annotated.vcf.gz"),
        "--outdir", str(outdir),
    )  # fmt: skip
    rows = _tsv(outdir / "gnomad_annotated_variants.tsv")
    assert {key: row["alt_fraction"] for key, row in rows.items()} == {
        ("100", "G"): "0.1",
        ("100", "T"): "0.4",
        ("200", "G"): "0.05",
        ("200", "T"): "0.4",
    }


@needs_htslib
def test_1289_missing_fields_in_unannotated_vcf_stay_empty(tmp_path):
    plain = tmp_path / "unannotated.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=100>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "chr1\t50\t.\tA\tG\t.\tPASS\t.\tGT\t0/1\n",
        newline="\n",
    )
    result = CommandResult("vcf table")
    with collecting(result):
        run_wgsextract(
            "vcf", "table", "--vcf-input", str(plain), "--outdir", str(tmp_path)
        )
    row = _tsv(tmp_path / "unannotated_variants.tsv")["50", "G"]
    assert not any(
        row[column]
        for column in (
            "phase_set", "alt_fraction", "transcript_id", "gnomad_popmax_af", "gnomad_nhomalt",
        )
    )  # fmt: skip
    assert {warning.code for warning in result.warnings} <= {"nothing_annotated"}


def test_imputed_genotypes_are_marked_with_their_probability():
    # #621: imputed rows must be told apart from sequenced ones.
    imputed = _record(
        "chr1", "800", ".", "C", "T", ".", "ImpLowConf", "IMP;RAF=0.3",
        "GT:DS:GP", "0/1:0.8:0.25,0.7,0.05",
    )  # fmt: skip
    called = _record("chr1", "900", ".", "G", "A", "30", "PASS", ".", "GT", "1/1")
    rows = _rows([imputed, called])
    assert (
        rows[("800", "T")]["imputed"],
        rows[("800", "T")]["genotype_probability"],
    ) == (1, 0.7)
    assert rows[("800", "T")]["filter"] == "ImpLowConf"
    assert (
        rows[("900", "A")]["imputed"],
        rows[("900", "A")]["genotype_probability"],
    ) == (0, None)


@pytest.mark.parametrize("gp", ["255,0,20", "-1,1,1", "0,inf,1"])
def test_732_non_probability_gp_never_becomes_a_probability(gp):
    record = _record(
        "chr1", "800", ".", "C", "T", ".", "PASS", ".",
        "GT:GP", f"0/1:{gp}",
    )  # fmt: skip
    assert _rows([record])[("800", "T")]["genotype_probability"] is None


# What `vcf acmg` writes (issue 629): Number=A classes on a vep-rs VCF.
ACMG_HEADER = HEADER.replace(
    "##FORMAT=<ID=GT,",
    '##INFO=<ID=ACMG_CLASS,Number=A,Type=String,Description="Research only">\n'
    '##INFO=<ID=ACMG_CRITERIA,Number=A,Type=String,Description="Criteria">\n'
    '##INFO=<ID=ACMG_TRANSCRIPT,Number=A,Type=String,Description="Transcript">\n'
    '##WGSE_ACMG=<Disclaimer="Research use only">\n'
    "##FORMAT=<ID=GT,",
)


@needs_htslib
def test_629_acmg_classes_become_research_only_columns(tmp_path):
    plain = tmp_path / "acmg_annotated.vcf"
    plain.write_text(
        ACMG_HEADER
        + _record(
            "chr1", "100", ".", "C", "T,G", "50", "PASS",
            "ACMG_CLASS=LP,VUS;ACMG_CRITERIA=PVS1&PM2_Supporting,PM2_Supporting;"
            "ACMG_TRANSCRIPT=ENST1,ENST1",
            "GT:DP:GQ", "1/2:30:99",
        )
        + _record("chr1", "200", ".", "A", "G", "50", "PASS", ".", "GT", "0/1")
    )  # fmt: skip
    out = tmp_path / "out"
    result = CommandResult("vcf table")
    with collecting(result):
        run_wgsextract(
            "vcf", "table", "--vcf-input", bgzip_index(plain), "--outdir", str(out)
        )  # fmt: skip

    rows = _tsv(out / "acmg_annotated_variants.tsv")
    acmg = ("acmg_class", "acmg_criteria", "acmg_transcript")
    assert [rows["100", "T"][c] for c in acmg] == ["LP", "PVS1&PM2_Supporting", "ENST1"]
    assert [rows["100", "G"][c] for c in acmg] == ["VUS", "PM2_Supporting", "ENST1"]
    assert [rows["200", "G"][c] for c in acmg] == ["", "", ""]

    db = sqlite3.connect(out / "acmg_annotated_variants.sqlite")
    metadata = dict(db.execute("SELECT key, value FROM metadata"))
    described = dict(db.execute("SELECT name, description FROM columns"))
    db.close()
    assert "acmg" in metadata["annotations"].split(",")
    assert metadata["acmg_disclaimer"].startswith("Research use only; not a diagnosis")
    assert described["acmg_class"].startswith("Research only, not a diagnosis")
    assert [w.code for w in result.warnings] == ["acmg_research_only"]


@needs_htslib
def test_629_a_table_without_acmg_classes_says_nothing_of_them(annotated_vcf, tmp_path):
    result = CommandResult("vcf table")
    with collecting(result):
        run_wgsextract(
            "vcf", "table", "--vcf-input", annotated_vcf, "--outdir", str(tmp_path)
        )  # fmt: skip
    db = sqlite3.connect(tmp_path / "sample_variants.sqlite")
    keys = {key for (key,) in db.execute("SELECT key FROM metadata")}
    classes = db.execute("SELECT count(acmg_class) FROM variants").fetchone()
    db.close()
    assert "acmg_disclaimer" not in keys
    assert classes == (0,)
    assert "acmg_research_only" not in {w.code for w in result.warnings}


# A vep-rs cache from `vep download` fills VEP's 1000 Genomes AF and MAX_AF and
# declares, but never fills, the gnomAD fields.
VEP_RS_CSQ = "Allele|Consequence|IMPACT|SYMBOL|AF|EUR_AF|gnomADe_AF|gnomADg_AF|MAX_AF"


@needs_htslib
def test_1354_vep_1000_genomes_frequencies_never_pass_for_gnomad(tmp_path):
    header = HEADER.replace(f"Format: {CSQ}", f"Format: {VEP_RS_CSQ}").replace(
        "##INFO=<ID=GNOMAD_AF", "##INFO=<ID=OTHER_AF"
    )
    plain = tmp_path / "vep.vcf"
    plain.write_text(
        header
        + _record("chr1", "100", "rs1", "A", "G", "50", "PASS",
                  "CSQ=G|missense_variant|MODERATE|GENEA|0.0755|0.3|||0.3",
                  "GT", "0/1")
        # Two co-located known variants: VEP joins their frequencies with '&'.
        + _record("chr1", "200", "rs2", "C", "T", "50", "PASS",
                  "CSQ=T|intron_variant|MODIFIER|GENEB|0.0002&0.4|0.5|||0.0004&0.6",
                  "GT", "1/1")
    )  # fmt: skip
    out = tmp_path / "out"
    result = CommandResult("vcf table")
    with collecting(result):
        run_wgsextract(
            "vcf", "table", "--vcf-input", bgzip_index(plain), "--outdir", str(out)
        )  # fmt: skip

    rows = _tsv(out / "vep_variants.tsv")
    freqs = ("gnomad_af", "gnomad_popmax_af", "gnomad_nhomalt", "kg_af", "max_af")
    assert [rows["100", "G"][c] for c in freqs] == ["", "", "", "0.0755", "0.3"]
    # The highest of co-located variants' values: common is never read as rare.
    assert [rows["200", "T"][c] for c in freqs] == ["", "", "", "0.4", "0.6"]
    db = sqlite3.connect(out / "vep_variants.sqlite")
    metadata = dict(db.execute("SELECT key, value FROM metadata"))
    db.close()
    assert metadata["population_af_sources"] == "1000genomes"
    assert (result.metrics["gnomad_af_rows"], result.metrics["kg_af_rows"]) == (0, 2)
    warning = next(w for w in result.warnings if w.code == "gnomad_af_missing")
    assert "1000 Genomes" in warning.message and "vcf gnomad" in warning.message
    # VEP declares gnomAD fields: a cache without gnomAD and variants gnomAD
    # lacks look alike, so the warning doesn't claim which (PR #1366 review).
    assert "may mean not annotated" in warning.message


def test_1354_vep_gnomad_frequency_fills_gnomad_af():
    records = [
        _record("chr1", "100", ".", "A", "G", "50", "PASS",
                "CSQ=G|missense_variant|MODERATE|GENEA|0.1|0.2|0.5|0.01&0.03|0.2",
                "GT", "0/1"),
    ]  # fmt: skip
    row = _rows(records, VEP_RS_CSQ)["100", "G"]
    # gnomAD genomes first, then exomes; never the 1000 Genomes AF.
    assert (row["gnomad_af"], row["kg_af"], row["max_af"]) == (0.03, 0.1, 0.2)
