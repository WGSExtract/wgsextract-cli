"""`info gene` (#1166, the desktop app's offline gene page): one gene's span,
transcripts, variants and exon coverage, from local files only."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, sam_to_bam
from tests.smoke_utils import run_cli, state_directory_setting
from wgsextract_cli.core import gene_map as gm
from wgsextract_cli.core.gene_map import download_gene_maps

# BRCA1 in hg38 (minus strand); the bundled annotation spans 43044295-43170327.
EXONS = ((43044294, 43045802), (43047642, 43047703), (43124016, 43125483))
CHR17, CHR1 = 83257441, 248956422


def _refgene() -> bytes:
    starts = ",".join(str(a) for a, _ in EXONS) + ","
    ends = ",".join(str(b) for _, b in EXONS) + ","
    row = ["0", "NM_007294", "chr17", "-", "43044294", "43125483", "43045677",
           "43124115", "3", starts, ends, "0", "BRCA1", "cmpl", "cmpl", "0,0,0,"]  # fmt: skip
    return gzip.compress(("\t".join(row) + "\n").encode())


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A reference library whose ``ref gene-map`` download was UCSC's refGene
    (the download is faked; parsing, bgzip and tabix are real)."""

    def download(url, dest, progress_callback=None, cancel_event=None):
        Path(dest).write_bytes(_refgene())
        return True

    monkeypatch.setattr(gm, "download_file", download)
    reflib = tmp_path / "reference"
    assert download_gene_maps(str(reflib))
    return reflib


def _config(tmp_path: Path, reflib: Path) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        state_directory_setting(tmp_path)
        + f"reference_library = {json.dumps(str(reflib))}\n",
        encoding="utf-8",
    )
    return config


def _bam(tmp_path: Path) -> str:
    """Reads at 30x over exon 1 and 5x over exon 2; none over exon 3."""
    lines = [
        "@HD\tVN:1.6\tSO:unsorted",
        f"@SQ\tSN:chr1\tLN:{CHR1}",
        f"@SQ\tSN:chr17\tLN:{CHR17}",
    ]
    reads = 0
    for (start, end), depth in zip(EXONS[:2], (30, 5), strict=True):
        # 50 bp reads starting every 50/depth bases cover the exon evenly.
        for first in range(start - 49, end):
            for _ in range(depth if (first - start) % 50 == 0 else 0):
                reads += 1
                lines.append(
                    f"r{reads}\t0\tchr17\t{first + 1}\t60\t50M\t*\t0\t0\t"
                    f"{'A' * 50}\t{'I' * 50}"
                )
    return sam_to_bam(lines, tmp_path / "sample.bam")


CSQ = "Allele|Consequence|IMPACT|SYMBOL|Feature|HGVSc|HGVSp"


def _vcf(tmp_path: Path, *, annotated: bool = True) -> str:
    info = [
        '##INFO=<ID=DP,Number=1,Type=Integer,Description="Depth">',
    ]
    if annotated:
        info += [
            f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {CSQ}">',
            '##INFO=<ID=CLNSIG,Number=.,Type=String,Description="ClinVar significance">',
            '##INFO=<ID=GNOMAD_AF,Number=A,Type=Float,Description="gnomAD Allele Frequency">',
            '##INFO=<ID=ACMG_CLASS,Number=A,Type=String,Description="fastVEP ACMG class">',
            "##bcftools_annotateCommand=annotate -a /data/lib/clinvar_20251101.vcf.gz -c INFO/CLNSIG in.vcf.gz",
        ]  # fmt: skip
    records = [
        ("43045712", "G", "A", "CSQ=A|missense_variant|MODERATE|BRCA1|NM_007294|c.1A>G|p.M1V;GNOMAD_AF=0.00001;ACMG_CLASS=VUS", "0/1"),
        ("43047650", "C", "T", "CSQ=T|stop_gained|HIGH|BRCA1|NM_007294|c.2C>T|p.Q2*;CLNSIG=Pathogenic;ACMG_CLASS=P", "0/1"),
        ("43124096", "A", "G", "CSQ=G|synonymous_variant|LOW|BRCA1|NM_007294|c.3A>G|p.=;CLNSIG=Benign;GNOMAD_AF=0.35;ACMG_CLASS=B", "1/1"),
        ("50000000", "T", "C", "CSQ=C|intergenic_variant|MODIFIER|||;GNOMAD_AF=0.5", "0/1"),
    ]  # fmt: skip
    body = "".join(
        f"chr17\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t"
        f"DP=30{';' + extra if annotated else ''}\tGT:DP\t{gt}:30\n"
        for pos, ref, alt, extra, gt in records
    )
    plain = tmp_path / "sample.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n"
        f"##contig=<ID=chr1,length={CHR1}>\n##contig=<ID=chr17,length={CHR17}>\n"
        + "\n".join(info)
        + '\n##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n" + body
    )
    return bgzip_index(plain)


def _gene(config: Path, *args: str) -> dict:
    code, stdout, stderr = run_cli(
        ["info", "gene", "--config", str(config), "--json", *args]
    )
    assert code == 0, stderr
    return json.loads(stdout)


@needs_htslib
def test_1166_gene_page_reads_transcripts_variants_and_exon_coverage(
    tmp_path: Path, library: Path
) -> None:
    result = _gene(
        _config(tmp_path, library),
        "--gene", "brca1", "--input", _bam(tmp_path),
        "--vcf-input", _vcf(tmp_path), "--min-depth", "20",
    )  # fmt: skip
    page = result["details"]
    assert page["gene"]["symbol"] == "BRCA1"
    assert page["gene"]["locus"] == "chr17:43044295-43170327"
    assert page["notices"] == []
    (transcript,) = page["transcripts"]["items"]
    assert transcript["accession"] == "NM_007294"
    assert transcript["exons"][0] == [43044295, 43045802]
    assert "refGene" in page["transcripts"]["source"]

    variants = page["variants"]
    assert {"vep", "clinvar", "gnomad", "acmg"} <= set(variants["annotations"])
    # Only the gene's variants, each tagged with the page filters it passes.
    assert {row["pos"]: row["filters"] for row in variants["rows"]} == {
        43045712: ["rare_damaging"],
        43047650: ["rare_damaging", "clinvar_plp", "acmg_plp"],
        43124096: [],
    }
    clinvar = next(p for p in variants["provenance"] if p["annotation"] == "clinvar")
    assert any("clinvar_20251101.vcf.gz" in line for line in clinvar["header"])
    assert not any("/data/lib" in line for line in clinvar["header"])
    assert variants["acmg_disclaimer"]

    coverage = page["coverage"]
    assert coverage["threshold"] == 20
    exons = {(e["start"], e["end"]): e for e in coverage["exons"]}
    assert exons[(43044295, 43045802)]["callable_fraction"] == 1
    assert not exons[(43044295, 43045802)]["below"]
    assert exons[(43047643, 43047703)]["mean"] == 5
    assert exons[(43047643, 43047703)]["below"]
    assert exons[(43124017, 43125483)]["min"] == 0
    assert coverage["exons_below"] == 2
    assert result["metrics"]["exons_below"] == 2


@needs_htslib
def test_1166_missing_data_is_named_with_the_command_that_makes_it(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty-library"
    empty.mkdir()
    vcf = _vcf(tmp_path, annotated=False)
    result = _gene(_config(tmp_path, empty), "--gene", "BRCA1", "--vcf-input", vcf)
    notices = result["details"]["notices"]
    commands = {n.get("annotation") or n["code"]: n["command"] for n in notices}
    assert commands["transcripts_missing"].startswith("wgsextract ref gene-map")
    assert commands["consequence"].startswith("wgsextract vep run --vcf-input")
    assert commands["clinvar"].startswith("wgsextract vcf clinvar")
    assert commands["gnomad"].startswith("wgsextract vcf gnomad")
    assert commands["acmg"].startswith("wgsextract vcf acmg")
    assert "alignment_missing" in commands
    assert result["details"]["coverage"] == {"status": "missing"}
    assert {w["code"] for w in result["warnings"]} >= {
        "gene_transcripts_missing",
        "gene_alignment_missing",
    }


@needs_htslib
def test_1166_export_writes_one_filter_as_tsv(tmp_path: Path, library: Path) -> None:
    out = tmp_path / "out"
    result = _gene(
        _config(tmp_path, library),
        "--gene", "BRCA1", "--vcf-input", _vcf(tmp_path),
        "--export", "clinvar-plp", "-o", str(out),
    )  # fmt: skip
    table = out / "sample_BRCA1_clinvar-plp_variants.tsv"
    assert [o["path"] for o in result["outputs"]] == [str(table)]
    with open(table, newline="") as f:
        rows = list(
            csv.DictReader((x for x in f if not x.startswith("#")), delimiter="\t")
        )
    assert [(r["pos"], r["clinvar_significance"]) for r in rows] == [
        ("43047650", "Pathogenic")
    ]


def test_1166_unknown_gene_is_a_usage_error(tmp_path: Path) -> None:
    code, _, stderr = run_cli(
        [
            "info",
            "gene",
            "--config",
            str(_config(tmp_path, tmp_path)),
            "--gene",
            "BRCAX1",
        ]
    )
    assert code == 2
    assert "BRCAX1" in stderr
