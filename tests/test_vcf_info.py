"""Tests for layered VCF reference detection, `vcf info`, `vcf variantqc`, and `vcf granite-qc`."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests.fake_tools import python_tool
from tests.htslib_helpers import needs_tools, write_fasta
from wgsextract_cli import api
from wgsextract_cli.api import (
    VcfGraniteQcParams,
    VcfInfoParams,
    VcfVariantQcParams,
)
from wgsextract_cli.core.utils import MissingDependencyError
from wgsextract_cli.core.variant_files import calculate_bam_md5
from wgsextract_cli.core.vcf_reference import DIAGNOSTIC_RSIDS, identify_vcf_reference


def _write_vcf(path: Path, header_lines: list[str], records: list[str]) -> Path:
    text = "\n".join([*header_lines, *records]) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def test_vcf_contig_md5_matches_exact_catalog_model(tmp_path):
    """A VCF with a full ##contig sequence dictionary gets an exact model code and tag."""
    header = (
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=248956422>\n"
        "##contig=<ID=chrM,length=16569>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )
    vcf = tmp_path / "sample.vcf"
    vcf.write_text(header, encoding="utf-8")

    # ##contig MD5 matches the same algorithm as BAM @SQ lines.
    bam_header = "@SQ\tSN:chr1\tLN:248956422\n@SQ\tSN:chrM\tLN:16569\n"
    assert calculate_bam_md5(vcf, header=header) == calculate_bam_md5(
        vcf, header=bam_header
    )

    detection = identify_vcf_reference(vcf, header=header, allow_body_sampling=False)
    assert detection.major_build == "GRCh38"
    assert detection.build == "hg38"
    assert detection.naming == "Chr"
    assert detection.evidence == "header chr1 length"


def test_vcf_header_reference_filename_detects_major_build(tmp_path):
    vcf = _write_vcf(
        tmp_path / "ref_only.vcf",
        [
            "##fileformat=VCFv4.2",
            "##reference=file:///data/genomes/chm13v2.0.fa.gz",
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
        ],
        ["chr1\t100\t.\tA\tG\t50\tPASS\t."],
    )
    detection = identify_vcf_reference(vcf, allow_body_sampling=False)
    assert detection.major_build == "T2T CHM13"
    assert detection.build == "t2t"
    assert detection.evidence == "header reference"


@needs_tools("samtools")
def test_vcf_body_ref_fingerprinting_distinguishes_builds_without_headers(tmp_path):
    """A headerless, unannotated VCF (ID='.') is identified by REF-base agreement against installed FASTAs."""
    reflib = tmp_path / "reflib"
    genomes = reflib / "genomes"
    genomes.mkdir(parents=True)

    # Distinct sequences for GRCh37 (hg19) vs GRCh38 (hg38) so REF alleles uniquely match GRCh38.
    seq37 = "ACGT" * 20  # 80 bp
    seq38 = "TGCA" * 20  # 80 bp (completely different base at every position)
    write_fasta(genomes / "hg19.fa", {"chr1": seq37})
    write_fasta(genomes / "hg38.fa", {"chr1": seq38})

    records = [
        f"chr1\t{pos}\t.\t{seq38[pos - 1]}\t{'A' if seq38[pos - 1] != 'A' else 'C'}\t50\tPASS\t."
        for pos in range(1, 26)
    ]
    vcf = _write_vcf(
        tmp_path / "stripped.vcf",
        ["##fileformat=VCFv4.2", "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"],
        records,
    )

    detection = identify_vcf_reference(vcf, ref_lib_dir=str(reflib))
    assert detection.major_build == "GRCh38"
    assert detection.build == "hg38"
    assert detection.naming == "Chr"
    assert detection.evidence.startswith("body REF match (25/25 vs GRCh38")


@pytest.mark.parametrize(
    ("target_build", "expected_major"), [("hg19", "GRCh37"), ("hg38", "GRCh38")]
)
def test_vcf_body_diagnostic_rsids_detect_major_build_without_fastas(
    tmp_path, target_build, expected_major
):
    """When no reference FASTAs are installed, diagnostic rsIDs in the VCF body identify GRCh37 vs GRCh38."""
    records = [
        f"chr{chrom}\t{positions[target_build]}\t{rsid}\tA\tG\t50\tPASS\t."
        for rsid, (chrom, positions) in list(DIAGNOSTIC_RSIDS.items())[:6]
    ]
    vcf = _write_vcf(
        tmp_path / f"rsids_{target_build}.vcf",
        ["##fileformat=VCFv4.2", "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"],
        records,
    )

    detection = identify_vcf_reference(vcf, ref_lib_dir=str(tmp_path / "empty_reflib"))
    assert detection.major_build == expected_major
    assert detection.build == target_build
    assert detection.evidence.startswith(f"body rsID match (6/6 vs {expected_major})")


def test_vcf_body_dbsnp_sqlite_detects_major_build(tmp_path):
    """An installed offline dbSNP SQLite index identifies non-diagnostic rsIDs in a headerless VCF."""
    reflib = tmp_path / "reflib"
    downloads = reflib / "downloads"
    downloads.mkdir(parents=True)
    db = downloads / "dbsnp_common_hg38.rsid.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE chroms (cid INTEGER PRIMARY KEY, name TEXT)")
    conn.execute(
        "CREATE TABLE rsids (rsid INTEGER PRIMARY KEY, cid INTEGER, pos INTEGER)"
    )
    conn.execute("INSERT INTO chroms VALUES (1, '1')")
    conn.executemany(
        "INSERT INTO rsids VALUES (?, 1, ?)",
        [(100001, 50000), (100002, 50100), (100003, 50200), (100004, 50300)],
    )
    conn.commit()
    conn.close()

    vcf = _write_vcf(
        tmp_path / "dbsnp_only.vcf",
        ["##fileformat=VCFv4.2", "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"],
        [
            "1\t50000\trs100001\tA\tG\t50\tPASS\t.",
            "1\t50100\trs100002\tC\tT\t50\tPASS\t.",
            "1\t50200\trs100003\tG\tA\t50\tPASS\t.",
            "1\t50300\trs100004\tT\tC\t50\tPASS\t.",
        ],
    )

    detection = identify_vcf_reference(vcf, ref_lib_dir=str(reflib))
    assert detection.major_build == "GRCh38"
    assert detection.naming == "Num"
    assert "body rsID match (4/4 vs GRCh38)" in detection.evidence


def test_vcf_body_coordinate_bounds_detects_grch37(tmp_path):
    vcf = _write_vcf(
        tmp_path / "bounds.vcf",
        ["##fileformat=VCFv4.2", "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"],
        ["chr1\t249100000\t.\tA\tG\t50\tPASS\t."],
    )
    detection = identify_vcf_reference(vcf, ref_lib_dir=str(tmp_path / "empty"))
    assert detection.major_build == "GRCh37"
    assert detection.build == "hg19"
    assert "body coordinate bounds" in detection.evidence


@needs_tools("bcftools", "bgzip", "tabix")
def test_vcf_info_reports_indexed_counts_samples_and_build(tmp_path):
    plain = _write_vcf(
        tmp_path / "sample.vcf",
        [
            "##fileformat=VCFv4.2",
            "##source=DeepVariant",
            "##contig=<ID=chr1,length=248956422>",
            "##contig=<ID=chrX,length=156040895>",
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA12878",
        ],
        [
            "chr1\t1000\trs1\tA\tG\t50\tPASS\t.\tGT\t0/1",
            "chr1\t2000\trs2\tC\tT\t60\tPASS\t.\tGT\t1/1",
            "chrX\t5000\trs3\tG\tA\t40\tPASS\t.\tGT\t0/1",
        ],
    )
    gz = tmp_path / "sample.vcf.gz"
    subprocess.run(["bgzip", "-c", str(plain)], stdout=gz.open("wb"), check=True)
    subprocess.run(["tabix", "-p", "vcf", str(gz)], check=True)

    result = api.vcf.info(VcfInfoParams(vcf_input=gz))
    assert result.details is not None
    assert result.details["reference_build"] == "GRCh38"
    assert result.details["samples"] == ["NA12878"]
    assert result.details["records"] == 3
    assert result.details["indexed"] is True
    assert result.details["file_content"] == "Auto, X"
    assert "GRCh38" in result.text
    assert "NA12878" in result.text
    assert "3 across 2 contigs" in result.text


@needs_tools("bcftools")
def test_vcf_variantqc_and_granite_qc_validate_optional_tools_and_run(
    tmp_path, monkeypatch
):
    """Both tools validate installation cleanly, then run and register their QC reports."""
    ref = write_fasta(tmp_path / "hg38.fa", {"chr1": "ACGT" * 25})
    vcf = _write_vcf(
        tmp_path / "sample.vcf",
        [
            "##fileformat=VCFv4.2",
            "##contig=<ID=chr1,length=248956422>",
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG002",
        ],
        ["chr1\t10\t.\tA\tG\t50\tPASS\t.\tGT\t0/1"],
    )

    # Without discvrseq or granite on PATH/config, clear MissingDependencyError is raised.
    monkeypatch.setattr(
        "wgsextract_cli.commands.vcf.qc_tools.find_discvrseq_jar", lambda *_: None
    )
    monkeypatch.setattr(
        "wgsextract_cli.commands.vcf.qc_tools.get_tool_path",
        lambda t: None if t in ("discvrseq", "granite") else t,
    )
    with pytest.raises(api.CommandFailed, match="DISCVRSeq") as vqc_err:
        api.vcf.variantqc(VcfVariantQcParams(vcf_input=vcf, ref=ref, outdir=tmp_path))
    assert isinstance(vqc_err.value.__cause__, MissingDependencyError)
    with pytest.raises(api.CommandFailed, match="granite") as gqc_err:
        api.vcf.granite_qc(VcfGraniteQcParams(vcf_input=vcf, outdir=tmp_path))
    assert isinstance(gqc_err.value.__cause__, MissingDependencyError)

    # Fake discvrseq wrapper writes the HTML report.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    discvrseq_exe = python_tool(
        fake_bin,
        "discvrseq",
        "import sys, pathlib\n"
        "assert sys.argv[1] == 'VariantQC'\n"
        "out = sys.argv[sys.argv.index('-O') + 1]\n"
        "pathlib.Path(out).write_text('<html><body>VariantQC OK</body></html>')\n",
    )
    granite_exe = python_tool(
        fake_bin,
        "granite",
        "import sys, json, pathlib\n"
        "assert sys.argv[1] == 'qcVCF'\n"
        "ped = json.loads(pathlib.Path(sys.argv[sys.argv.index('-p') + 1]).read_text())\n"
        "assert ped[0]['sample_name'] == 'HG002'\n"
        "assert '--ti_tv' in sys.argv and '--het_hom' in sys.argv\n"
        "out = sys.argv[sys.argv.index('-o') + 1]\n"
        "pathlib.Path(out).write_text(json.dumps({'HG002': {'TiTv': 2.08, 'HetHom': 1.54}}))\n",
    )
    monkeypatch.setattr(
        "wgsextract_cli.commands.vcf.qc_tools.get_tool_path",
        lambda t: (
            str(discvrseq_exe)
            if t == "discvrseq"
            else str(granite_exe)
            if t == "granite"
            else t
        ),
    )

    vqc_result = api.vcf.variantqc(
        VcfVariantQcParams(vcf_input=vcf, ref=ref, outdir=tmp_path)
    )
    assert (tmp_path / "sample.variantqc.html").is_file()
    assert any(o.role == "variantqc_report" for o in vqc_result.outputs)

    # Multi-VCF VariantQC passes every selected VCF with -V.
    vcf2 = _write_vcf(
        tmp_path / "sample2.vcf",
        [
            "##fileformat=VCFv4.2",
            "##contig=<ID=chr1,length=248956422>",
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG003",
        ],
        ["chr1\t20\t.\tC\tT\t50\tPASS\t.\tGT\t0/1"],
    )
    multi_result = api.vcf.variantqc(
        VcfVariantQcParams(vcf_input=vcf, extra_vcf=(vcf2,), ref=ref, outdir=tmp_path)
    )
    assert multi_result.metrics["vcfs"] == 2
    assert any(o.role == "extra_vcf" for o in multi_result.inputs)

    gqc_result = api.vcf.granite_qc(VcfGraniteQcParams(vcf_input=vcf, outdir=tmp_path))
    assert (tmp_path / "sample.granite_qc.json").is_file()
    assert any(o.role == "granite_qc_json" for o in gqc_result.outputs)
    assert gqc_result.metrics["TiTv"] == 2.08
    assert "TiTv" in gqc_result.text


@needs_tools("bgzip", "tabix", "samtools", "discvrseq", "java17")
@pytest.mark.parametrize("index", ["none", "csi", "short-tbi"])
def test_vcf_variantqc_real_discvrseq_reads_any_indexed_bgzipped_vcf(tmp_path, index):
    """Real VariantQC on a .vcf.gz without the <vcf>.tbi GATK reads: it
    stopped with "An index is required but was not found" when the VCF had no
    index, only a CSI one, or a sample.vcf.tbi."""
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "ACGTTGCA" * 125})
    subprocess.run(["samtools", "dict", "-o", tmp_path / "ref.dict", ref], check=True)
    plain = _write_vcf(
        tmp_path / "sample.vcf",
        [
            "##fileformat=VCFv4.2",
            "##contig=<ID=chr1,length=1000>",
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG002",
        ],
        [
            "chr1\t10\t.\tT\tC\t50\tPASS\t.\tGT\t0/1",
            "chr1\t20\t.\tA\tG\t50\tPASS\t.\tGT\t1/1",
            "chr1\t30\t.\tG\tGA\t50\tPASS\t.\tGT\t0/1",
        ],
    )
    vcf = tmp_path / "sample.vcf.gz"
    with vcf.open("wb") as out:
        subprocess.run(["bgzip", "-c", plain], stdout=out, check=True)
    if index == "csi":
        subprocess.run(["tabix", "-C", "-p", "vcf", vcf], check=True)
    elif index == "short-tbi":
        subprocess.run(["tabix", "-p", "vcf", vcf], check=True)
        Path(f"{vcf}.tbi").rename(tmp_path / "sample.vcf.tbi")

    result = api.vcf.variantqc(
        VcfVariantQcParams(vcf_input=vcf, ref=ref, outdir=tmp_path / "qc")
    )

    report = (tmp_path / "qc" / "sample.variantqc.html").read_text(encoding="utf-8")
    assert any(o.role == "variantqc_report" for o in result.outputs)
    assert '{"name":"nSNPs","data":[2,0,2]}' in report
    assert '{"name":"nInsertions","data":[1,0,1]}' in report
