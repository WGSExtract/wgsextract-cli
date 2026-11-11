"""`ref gnomad-sv` and `vcf sv-freq`: SV population frequencies (#618).

Real bcftools, bgzip and tabix throughout; only the network is faked, by a
local HTTP server holding stand-in gnomAD sites VCFs.
"""

import csv
import dataclasses
import gzip
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tests.disk_volumes import Volumes
from tests.http_server import serving
from wgsextract_cli import api
from wgsextract_cli.core import gnomad_sv

CHR1 = 248956422  # GRCh38, so the VCFs are recognised as hg38

_SR_HEADER = f"""##fileformat=VCFv4.2
##contig=<ID=chr1,length={CHR1}>
##FILTER=<ID=PASS,Description="All filters passed">
##FILTER=<ID=HIGH_NCR,Description="High no-call rate">
##FILTER=<ID=MULTIALLELIC,Description="Multiallelic site">
##ALT=<ID=DEL,Description="Deletion">
##INFO=<ID=SVTYPE,Number=1,Type=String,Description="t">
##INFO=<ID=END,Number=1,Type=Integer,Description="t">
##INFO=<ID=SVLEN,Number=1,Type=Integer,Description="t">
##INFO=<ID=AF,Number=A,Type=Float,Description="t">
##INFO=<ID=GRPMAX_AF,Number=1,Type=Float,Description="t">
##INFO=<ID=CN_NONREF_FREQ,Number=1,Type=Float,Description="t">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
"""
# A common deletion, a rare insertion, a duplication, an inversion, a
# multi-allelic CNV (carrier frequency only), a filtered deletion and a
# breakend; the last two are left out of the table.
_SR_RECORDS = [
    # gnomAD lists some SVs twice at the same breakpoints, once rare.
    "chr1\t10000\tgDELdup\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;END=12000;SVLEN=2000;AF=0.00002;GRPMAX_AF=0.0001",
    "chr1\t10000\tgDEL\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;END=12000;SVLEN=2000;AF=0.3;GRPMAX_AF=0.5",
    "chr1\t20000\tgINS\tN\t<INS:ME:ALU>\t.\tPASS\tSVTYPE=INS;END=20001;SVLEN=300;AF=0.0004;GRPMAX_AF=0.002",
    "chr1\t30000\tgDUP\tN\t<DUP>\t.\tPASS\tSVTYPE=DUP;END=30400;SVLEN=400;AF=0.005;GRPMAX_AF=0.02",
    "chr1\t40000\tgINV\tN\t<INV>\t.\tPASS\tSVTYPE=INV;END=45000;SVLEN=5000;AF=0.02;GRPMAX_AF=0.03",
    "chr1\t60000\tgCNV\tN\t<CNV>\t.\tMULTIALLELIC\tSVTYPE=CNV;END=80000;SVLEN=20000;CN_NONREF_FREQ=0.4",
    "chr1\t90000\tgBAD\tN\t<DEL>\t.\tHIGH_NCR\tSVTYPE=DEL;END=91000;SVLEN=1000;AF=0.9;GRPMAX_AF=0.9",
    "chr1\t95000\tgBND\tN\t<BND>\t.\tPASS\tSVTYPE=BND;END=95000;SVLEN=-1;AF=0.1;GRPMAX_AF=0.1",
]

_CALLS_HEADER = f"""##fileformat=VCFv4.2
##contig=<ID=chr1,length={CHR1}>
##INFO=<ID=SVTYPE,Number=1,Type=String,Description="t">
##INFO=<ID=END,Number=1,Type=Integer,Description="t">
##INFO=<ID=SVLEN,Number=1,Type=Integer,Description="t">
##INFO=<ID=CHR2,Number=1,Type=String,Description="t">
##FORMAT=<ID=GT,Number=1,Type=String,Description="t">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG002
"""
# Delly-style symbolic calls with imprecise breakpoints.
_CALLS = [
    "chr1\t10100\tdel_common\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;END=12050;SVLEN=-1950\tGT\t1/1",
    # Overlaps the common deletion by less than half of its own length.
    "chr1\t11500\tdel_novel\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;END=16000;SVLEN=-4500\tGT\t0/1",
    "chr1\t20150\tins_rare\tN\t<INS>\t.\tPASS\tSVTYPE=INS;END=20150;SVLEN=310\tGT\t0/1",
    "chr1\t29950\tdup_low\tN\t<DUP>\t.\tPASS\tSVTYPE=DUP;END=30420;SVLEN=470\tGT\t0/1",
    "chr1\t40500\tinv_common\tN\t<INV>\t.\tPASS\tSVTYPE=INV;END=44000;SVLEN=3500\tGT\t0/1",
    "chr1\t65000\tcnv_common\tN\t<CN0>\t.\tPASS\tSVTYPE=CNV;END=78000;SVLEN=13000\tGT\t1/1",
    "chr1\t90100\tdel_filtered_in_gnomad\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;END=91000;SVLEN=-900\tGT\t0/1",
    "chr1\t95000\tbnd\tN\tN]chr2:100]\t.\tPASS\tSVTYPE=BND;CHR2=chr2\tGT\t0/1",
    "chr1\t99000\tsnv\tA\tG\t.\tPASS\t.\tGT\t0/1",
]


def _bgzip_vcf(path, text):
    path.with_suffix("").write_text(text)
    subprocess.run(["bgzip", "-f", str(path.with_suffix(""))], check=True)
    subprocess.run(["tabix", "-f", "-p", "vcf", str(path)], check=True)
    return path


@pytest.fixture
def gnomad_server(monkeypatch, tmp_path):
    """Serve a stand-in gnomAD-SV v4.1 sites VCF, pinned to its digest."""
    vcf = _bgzip_vcf(
        tmp_path / "sv.sites.vcf.gz", _SR_HEADER + "\n".join(_SR_RECORDS) + "\n"
    )
    data = vcf.read_bytes()
    with serving(files={"sv.sites.vcf.gz": data}) as server:
        cat = gnomad_sv.CATALOGUES["sr", "hg38"]
        source = gnomad_sv.SourceFile(
            server.url_for("sv.sites.vcf.gz"),
            hashlib.sha256(data).hexdigest(),
            len(data),
        )
        pinned = dataclasses.replace(cat, files=(source,))
        monkeypatch.setitem(gnomad_sv.CATALOGUES, ("sr", "hg38"), pinned)
        yield server


@pytest.fixture
def library(gnomad_server, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    api.ref.gnomad_sv(api.RefGnomadSvParams(ref=lib, build="hg38"))
    return lib


@pytest.fixture
def calls(tmp_path):
    return _bgzip_vcf(tmp_path / "sv.vcf.gz", _CALLS_HEADER + "\n".join(_CALLS) + "\n")


def _records(vcf):
    out = subprocess.run(
        ["bcftools", "query", "-f", "%ID\t%INFO\n", str(vcf)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    records = {}
    for line in out.splitlines():
        ident, info = line.split("\t")
        records[ident] = dict(
            item.partition("=")[::2] for item in info.split(";") if info != "."
        )
    return records


def _output(result, role):
    return next(o.path for o in result.outputs if o.role == role)


def _sv_freq(vcf, lib, outdir, **params):
    return api.vcf.sv_freq(
        api.VcfSvFreqParams(input=vcf, ref=lib, outdir=outdir, **params)
    )


def test_ref_gnomad_sv_keeps_pass_svs_with_a_frequency(library):
    table = library / "gnomad-sv" / "gnomad_sv_v4.1_hg38.tsv.gz"
    with gzip.open(table, "rt") as stream:
        rows = [line.split("\t") for line in stream if not line.startswith("#")]

    assert [r[7].strip() for r in rows] == [
        "gDELdup",
        "gDEL",
        "gINS",
        "gDUP",
        "gINV",
        "gCNV",
    ]
    cnv = rows[-1]
    assert cnv[3] == "CNV" and cnv[5] == "0.4"  # the carrier frequency
    assert (library / "gnomad-sv" / f"{table.name}.tbi").is_file()


def test_ref_gnomad_sv_refuses_a_download_that_does_not_match_its_pin(
    gnomad_server, monkeypatch, tmp_path
):
    cat = gnomad_sv.CATALOGUES["sr", "hg38"]
    wrong = dataclasses.replace(cat.files[0], sha256="0" * 64)
    monkeypatch.setitem(
        gnomad_sv.CATALOGUES, ("sr", "hg38"), dataclasses.replace(cat, files=(wrong,))
    )
    result = api.ref.gnomad_sv(
        api.RefGnomadSvParams(ref=tmp_path, build="hg38"), check=False
    )

    assert result.status == "failed"
    assert not (tmp_path / "gnomad-sv" / cat.table).exists()


def test_ref_gnomad_sv_checks_disk_space_first(gnomad_server, monkeypatch, tmp_path):
    volumes = Volumes(monkeypatch)
    volumes.free[tmp_path] = 0.1  # GiB; the table alone needs 0.5 GB
    result = api.ref.gnomad_sv(
        api.RefGnomadSvParams(ref=tmp_path, build="hg38"), check=False
    )

    assert result.status == "failed"
    assert "GB free" in result.error.message
    assert not gnomad_server.gets()


def test_ref_gnomad_sv_deletes(library):
    api.ref.gnomad_sv(api.RefGnomadSvParams(ref=library, build="hg38", delete=True))

    assert not list((library / "gnomad-sv").glob("gnomad_sv_*"))


def test_ref_status_reports_an_unfinished_install_as_not_installed(library):
    def gnomad(lib):
        return api.ref.status(api.RefStatusParams(ref=lib)).details["gnomadSv"]

    assert gnomad(library)["sr_hg38"] is True
    # #688 review: a table left without its index counted as installed.
    (library / "gnomad-sv" / "gnomad_sv_v4.1_hg38.tsv.gz.tbi").unlink()
    assert gnomad(library)["sr_hg38"] is False


def test_sv_calls_get_their_population_frequency(library, calls, tmp_path):
    result = _sv_freq(calls, library, tmp_path / "out")
    records = _records(_output(result, "annotated_vcf"))

    common = records["del_common"]
    assert common["SV_AF"] == "0.3" and common["SV_POPMAX_AF"] == "0.5"
    # The common record, not its rare twin (HG002 validation: chr1:184845609).
    assert common["SV_AF_ID"] == "gDEL" and common["SV_FREQ"] == "common"
    assert "SV_COMMON" in common
    assert common["SV_AF_SOURCE"] == "gnomAD-SV_v4.1"
    assert records["ins_rare"]["SV_FREQ"] == "rare"
    assert "SV_COMMON" not in records["ins_rare"]
    assert records["dup_low"]["SV_FREQ"] == "low"
    assert records["inv_common"]["SV_AF_ID"] == "gINV"
    assert records["cnv_common"]["SV_AF_ID"] == "gCNV"
    # Too little reciprocal overlap; and gnomAD's own filtered site doesn't count.
    assert records["del_novel"]["SV_FREQ"] == "novel"
    assert records["del_filtered_in_gnomad"]["SV_FREQ"] == "novel"
    # Breakends and small variants are left as they were.
    assert "SV_FREQ" not in records["bnd"] and "SV_FREQ" not in records["snv"]

    assert result.metrics["svs"] == 7
    assert result.metrics["sv_matched"] == 5
    assert result.metrics["sv_common"] == 3 and result.metrics["sv_novel"] == 2
    with open(_output(result, "sv_frequencies"), newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert {r["id"]: r["sv_freq"] for r in rows}["ins_rare"] == "rare"
    assert len(rows) == 7


def test_a_stricter_overlap_leaves_imprecise_calls_unmatched(library, calls, tmp_path):
    result = _sv_freq(calls, library, tmp_path / "out", min_overlap=0.95)
    records = _records(_output(result, "annotated_vcf"))

    assert records["inv_common"]["SV_FREQ"] == "novel"  # 70% overlap
    assert records["del_common"]["SV_FREQ"] == "common"  # 95%


def test_sequence_resolved_long_read_calls_match(library, tmp_path):
    """Sniffles/pbsv and benchmark VCFs spell SVs out as sequence, often with
    no SVTYPE; a tandem duplication called as an insertion matches gnomAD's
    duplication."""
    deletion = "A" * 1951
    vcf = _bgzip_vcf(
        tmp_path / "seq.vcf.gz",
        _CALLS_HEADER
        + f"chr1\t10050\tseq_del\t{deletion}\tA\t.\tPASS\t.\tGT\t0/1\n"
        + f"chr1\t20100\tseq_ins\tA\tA{'C' * 290}\t.\tPASS\t.\tGT\t1/1\n"
        + f"chr1\t30010\tins_dup\tA\tA{'G' * 390}\t.\tPASS\tSVTYPE=INS\tGT\t0/1\n",
    )
    records = _records(_output(_sv_freq(vcf, library, tmp_path / "o"), "annotated_vcf"))

    assert records["seq_del"]["SV_AF_ID"] == "gDEL"
    assert records["seq_ins"]["SV_AF_ID"] == "gINS"
    assert records["ins_dup"]["SV_AF_ID"] == "gDUP"


def _bare_table(path, source, build):
    path.with_suffix("").write_text(
        f"##source={source}\n##build={build}\n"
        "#CHROM\tPOS\tEND\tSVTYPE\tSVLEN\tAF\tPOPMAX_AF\tID\n"
        "1\t10000\t12000\tDEL\t2000\t0.2\t0.3\tbareDEL\n"
        # No one carries it (#618: gnomAD-LR lists such sites): not a match.
        "1\t20100\t20100\tINS\t300\t0\t0\tnobody\n",
        newline="\n",
    )
    subprocess.run(["bgzip", "-f", str(path.with_suffix(""))], check=True)
    subprocess.run(["tabix", "-s1", "-b2", "-e3", str(path)], check=True)
    return path


def test_a_table_with_bare_contig_names_matches_chr_named_calls(calls, tmp_path):
    table = _bare_table(tmp_path / "bare.tsv.gz", "custom-SV", "hg38")
    result = _sv_freq(calls, None, tmp_path / "o", gnomad_sv_file=table)
    records = _records(_output(result, "annotated_vcf"))

    assert records["del_common"]["SV_AF_SOURCE"] == "custom-SV"
    assert records["del_common"]["SV_AF"] == "0.2"
    assert records["ins_rare"]["SV_FREQ"] == "novel"


def test_a_table_for_another_build_is_refused(library, calls, tmp_path):
    """#688 review: a GRCh37 gnomAD-SV v2.1 table silently annotated GRCh38
    calls with GRCh37 coordinates' frequencies."""
    table = _bare_table(tmp_path / "v2.tsv.gz", "gnomAD-SV_v2.1", "hg19")
    result = api.vcf.sv_freq(
        api.VcfSvFreqParams(
            input=calls, ref=library, outdir=tmp_path / "o", gnomad_sv_file=table
        ),
        check=False,
    )

    assert result.status == "failed"
    assert "hg19" in result.error.message and "hg38" in result.error.message
    assert not (tmp_path / "o" / "sv_freq_annotated.vcf.gz").exists()


def test_a_changed_table_is_not_used(library, calls, tmp_path):
    table = library / "gnomad-sv" / "gnomad_sv_v4.1_hg38.tsv.gz"
    table.write_bytes(table.read_bytes() + b"\0")
    result = api.vcf.sv_freq(
        api.VcfSvFreqParams(input=calls, ref=library, outdir=tmp_path / "o"),
        check=False,
    )

    assert result.status == "failed"
    assert "ref gnomad-sv --install" in result.error.message


def test_vcf_table_and_report_show_sv_frequencies(library, calls, tmp_path):
    folder = tmp_path / "sample"
    result = _sv_freq(calls, library, folder)
    vcf = _output(result, "annotated_vcf")
    table = api.vcf.table(api.VcfTableParams(input=vcf, outdir=folder))
    with open(_output(table, "variant_table"), newline="") as stream:
        rows = {
            r["pos"]: r
            for r in csv.DictReader(
                (line for line in stream if not line.startswith("##")),
                delimiter="\t",
            )
        }
    assert rows["10100"]["sv_freq"] == "common" and rows["10100"]["sv_af"] == "0.3"
    assert rows["99000"]["sv_freq"] == ""

    report = api.report.build(api.ReportParams(workspace=folder))
    document = json.loads(Path(_output(report, "report_json")).read_text())
    section = document["sections"]["structural_variants"]
    assert section["status"] == "ok"
    assert [f["value"] for f in section["findings"]] == ["rare (AF < 0.001)"]
    assert section["findings"][0]["label"].startswith("INS chr1:20150")
    assert section["counts"]["common"] == 3 and section["counts"]["novel"] == 2
