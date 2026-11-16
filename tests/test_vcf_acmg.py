"""`vcf acmg` (issue 629: opt-in, research-only ACMG classes from fastVEP) with
the real fastVEP and bcftools on a one-gene genome (tests/fixtures/vep_rs).
The input is `vep run --engine vep-rs` output for that gene
(tests/fixtures/acmg/in_vep.vcf), and the databases are built by
`fastvep sa-build` from tiny source files in tests/fixtures/acmg. Skipped
without fastVEP (`pixi install -e fastvep`), except the opt-in refusal."""

import gzip
import json
import shutil
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, needs_tools
from wgsextract_cli import api
from wgsextract_cli.api.vcf import VcfAcmgParams
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.main import main

FIXTURES = Path(__file__).parent / "fixtures" / "acmg"
GENE = Path(__file__).parent / "fixtures" / "vep_rs"
needs_fastvep = needs_tools("fastvep")
pytestmark = needs_htslib

# Source file and fastVEP source name of each of the eight databases.
SOURCES = {
    "clinvar": "clinvar.vcf",
    "clinvar_protein": "variant_summary.txt",
    "gnomad": "gnomad.vcf",
    "gnomad_genes": "constraint.tsv",
    "omim": "clingen.tsv",
    "revel": "revel.csv",
    "spliceai": "spliceai.vcf",
    "phylop": "phylop.wigFix",
}


def _sa_dir(tmp_path: Path, skip: str = "", gnomad_contig: str = "1") -> Path:
    sa = tmp_path / "sa"
    sa.mkdir()
    fastvep = get_tool_path("fastvep") or "fastvep"
    for source, name in SOURCES.items():
        if source == skip:
            continue
        src = FIXTURES / name
        if source == "gnomad" and gnomad_contig != "1":
            src = tmp_path / name
            text = (FIXTURES / name).read_text()
            src.write_text(text.replace("\n1\t", f"\n{gnomad_contig}\t"))
        run_command(
            [fastvep, "sa-build", "--source", source, "-i", str(src),
             "-o", str(sa / source), "--assembly", "GRCh38", "--no-progress"],
            capture_output=True,
        )  # fmt: skip
    return sa


def _inputs(tmp_path: Path, vcf_text: str | None = None) -> dict[str, Path]:
    for name in ("genome.fa", "genes.gff3"):
        shutil.copy(GENE / name, tmp_path / name)
    plain = tmp_path / "in_vep.vcf"
    plain.write_text(vcf_text or (FIXTURES / "in_vep.vcf").read_text())
    return {
        "input": Path(bgzip_index(plain)),
        "ref": tmp_path / "genome.fa",
        "gff3": tmp_path / "genes.gff3",
    }


def _params(tmp_path: Path, sa: Path, **inputs: Path) -> VcfAcmgParams:
    return VcfAcmgParams(
        research_acmg=True, sa_dir=sa, outdir=tmp_path / "out", **inputs
    )


def _acmg(vcf: Path) -> dict[int, dict[str, str]]:
    """Each record's ACMG_* INFO values, by position."""
    out = {}
    with gzip.open(vcf, "rt") as f:
        for line in f:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            info = dict(kv.partition("=")[::2] for kv in cols[7].split(";"))
            out[int(cols[1])] = {k: v for k, v in info.items() if k.startswith("ACMG")}
    return out


@needs_fastvep
def test_acmg_classes_go_into_the_vep_rs_vcf_with_the_disclaimer(tmp_path):
    result = api.vcf.acmg(_params(tmp_path, _sa_dir(tmp_path), **_inputs(tmp_path)))

    vcf = tmp_path / "out" / "acmg_annotated.vcf.gz"
    acmg = _acmg(vcf)
    # TINY1 Gln3Ter in a constrained, ClinGen-definitive gene, absent from gnomAD.
    assert acmg[1017]["ACMG_CLASS"] == "LP"
    assert {"PVS1", "PM2_Supporting"} <= set(acmg[1017]["ACMG_CRITERIA"].split("&"))
    assert acmg[1017]["ACMG_TRANSCRIPT"] == "ENST00000000001"
    # gnomAD AF 0.3: stand-alone benign.
    assert acmg[1150] == {
        "ACMG_CLASS": "B",
        "ACMG_CRITERIA": "BA1",
        "ACMG_TRANSCRIPT": "ENST00000000001",
    }
    # One value per ALT of a multi-allelic record.
    assert len(acmg[2500]["ACMG_CLASS"].split(",")) == 2
    with gzip.open(vcf, "rt") as f:
        text = f.read()
    assert "Research use only; not a diagnosis" in text
    assert "CSQ=T|stop_gained" in text  # vep-rs's consequences stay

    summary = json.loads((tmp_path / "out" / "acmg_summary.json").read_text())
    assert summary["disclaimer"].startswith("Research use only; not a diagnosis")
    assert summary["classes"]["LP"] >= 1 and summary["classes"]["B"] == 1
    assert summary["transcript_choice"].get("other_transcript", 0) == 0
    assert {o.role for o in result.outputs} >= {"annotated_vcf", "acmg_summary"}
    assert "acmg_research_only" in {w.code for w in result.warnings}
    assert result.metrics["acmg_b"] == 1


@needs_fastvep
def test_acmg_falls_back_to_fastveps_transcript_and_counts_it(tmp_path):
    # vep-rs picked a transcript fastVEP's GFF3 doesn't have.
    text = (FIXTURES / "in_vep.vcf").read_text()
    edited = text.replace(
        "|Transcript|ENST00000000001|", "|Transcript|ENST00000000099|"
    )
    inputs = _inputs(tmp_path, edited)

    api.vcf.acmg(_params(tmp_path, _sa_dir(tmp_path), **inputs))

    acmg = _acmg(tmp_path / "out" / "acmg_annotated.vcf.gz")
    assert acmg[1017]["ACMG_TRANSCRIPT"] == "ENST00000000001"
    summary = json.loads((tmp_path / "out" / "acmg_summary.json").read_text())
    assert summary["transcript_choice"]["other_transcript"] >= 3


def test_acmg_refuses_without_the_research_opt_in(tmp_path, capsys):
    inputs = _inputs(tmp_path)
    with pytest.raises(SystemExit) as exited:
        main(
            ["vcf", "acmg", "--vcf-input", str(inputs["input"]),
             "--sa-dir", str(tmp_path), "--outdir", str(tmp_path / "out"), "--json"]
        )  # fmt: skip
    assert exited.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert "--research-acmg" in result["error"]["message"]
    assert not (tmp_path / "out").exists()


@needs_fastvep
@pytest.mark.parametrize(
    ("sa_args", "named"),
    [
        ({"skip": "revel"}, "REVEL"),
        ({"gnomad_contig": "2"}, "gnomAD"),
    ],
)
def test_acmg_refuses_databases_missing_a_source(tmp_path, sa_args, named):
    sa = _sa_dir(tmp_path, **sa_args)
    result = api.vcf.acmg(_params(tmp_path, sa, **_inputs(tmp_path)), check=False)
    assert result.exit_code == 1
    assert result.error is not None and named in result.error.message
    assert not (tmp_path / "out" / "acmg_annotated.vcf.gz").exists()


@needs_fastvep
def test_acmg_refuses_a_vcf_without_vep_annotation(tmp_path):
    plain = (
        "##fileformat=VCFv4.2\n##contig=<ID=1,length=248956422>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n1\t1017\t.\tC\tT\t50\tPASS\t.\n"
    )
    params = _params(tmp_path, tmp_path, **_inputs(tmp_path, plain))
    result = api.vcf.acmg(params, check=False)
    assert result.error is not None and "vep run" in result.error.message


@needs_fastvep
def test_acmg_reports_fastveps_own_error_when_it_cannot_start(tmp_path):
    # Review of #629: a GFF3 fastVEP can't read was reported as "no ACMG column".
    inputs = _inputs(tmp_path)
    inputs["gff3"].write_text("##gff-version 3\nthis is not gff\n")
    result = api.vcf.acmg(_params(tmp_path, _sa_dir(tmp_path), **inputs), check=False)
    assert result.error is not None
    assert "fastVEP failed before reading the input" in result.error.message
