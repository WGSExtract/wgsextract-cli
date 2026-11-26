"""`vcf acmg` (issue 629: opt-in, research-only ACMG classes from fastVEP) with
the real fastVEP and bcftools on a one-gene genome (tests/fixtures/vep_rs).
The input is `vep run --engine vep-rs` output for that gene
(tests/fixtures/acmg/in_vep.vcf), and the databases are built by
`fastvep sa-build` from tiny source files in tests/fixtures/acmg. Skipped
without fastVEP (`pixi install -e fastvep`), except the opt-in refusal."""

import gc
import gzip
import json
import os
import shutil
import stat
import warnings
from io import IOBase
from pathlib import Path

import pytest

from tests.fake_tool_launchers import python_tool
from tests.htslib_helpers import bgzip_index, needs_htslib, needs_tools
from wgsextract_cli import api
from wgsextract_cli.api.vcf import VcfAcmgParams
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.dependencies import clear_tool_caches, get_tool_path
from wgsextract_cli.core.tool_commands import _tool_command_parts
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


def _sa_dir(
    tmp_path: Path,
    skip: str | tuple[str, ...] = (),
    gnomad_contig: str = "1",
    assembly: str = "GRCh38",
    repeatmasker: bool = False,
) -> Path:
    sa = tmp_path / "sa"
    sa.mkdir()
    skips = (skip,) if isinstance(skip, str) else skip
    fastvep = get_tool_path("fastvep") or "fastvep"
    if repeatmasker:
        bed = tmp_path / "repeats.bed"
        bed.write_text("1\t1100\t1200\tAluY\n")
        run_command(
            [fastvep, "sa-build", "--source", "custom_bed", "--name", "repeatmasker",
             "-i", str(bed), "-o", str(sa / "repeatmasker"), "--no-progress"],
            capture_output=True,
        )  # fmt: skip
    for source, name in SOURCES.items():
        if source in skips:
            continue
        src = FIXTURES / name
        if source == "gnomad" and gnomad_contig != "1":
            src = tmp_path / name
            text = (FIXTURES / name).read_text()
            src.write_text(text.replace("\n1\t", f"\n{gnomad_contig}\t"))
        run_command(
            [fastvep, "sa-build", "--source", source, "-i", str(src),
             "-o", str(sa / source), "--assembly", assembly, "--no-progress"],
            capture_output=True,
        )  # fmt: skip
    return sa


def _inputs(tmp_path: Path, vcf_text: str | None = None) -> dict[str, Path]:
    for name in ("genome.fa", "genes.gff3"):
        shutil.copy(GENE / name, tmp_path / name)
    plain = tmp_path / "in_vep.vcf"
    plain.write_text(vcf_text or (FIXTURES / "in_vep.vcf").read_text(), newline="\n")
    return {
        "input": Path(bgzip_index(plain)),
        "ref": tmp_path / "genome.fa",
        "gff3": tmp_path / "genes.gff3",
    }


def _params(
    tmp_path: Path, sa: Path, site_data: str = "auto", out: str = "out", **inputs: Path
) -> VcfAcmgParams:
    return VcfAcmgParams(
        research_acmg=True,
        sa_dir=sa,
        outdir=tmp_path / out,
        site_data=site_data,
        **inputs,
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
    inputs = _inputs(tmp_path)
    vcf = tmp_path / "out" / "acmg_annotated.vcf.gz"
    vcf.parent.mkdir()
    shutil.copy2(inputs["input"], vcf)
    vcf.chmod(0o600)
    old_index = vcf.with_suffix(".csi")
    run_command(["tabix", "-C", "-p", "vcf", str(vcf)])
    Path(f"{vcf}.csi").rename(old_index)

    result = api.vcf.acmg(_params(tmp_path, _sa_dir(tmp_path), **inputs))

    # #1215: the old alternate index must not shadow the new VCF's index.
    assert not old_index.exists()
    if os.name != "nt":
        assert stat.S_IMODE(Path(f"{vcf}.tbi").stat().st_mode) & 0o077 == 0
    indexed_records = run_command(
        [
            "bcftools",
            "query",
            "-r",
            "1:1017-1017",
            "-f",
            "%INFO/ACMG_CLASS\n",
            str(vcf),
        ],
        capture_output=True,
    )
    assert indexed_records.stdout.strip() == "LP"
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
    assert summary["sources_not_used"] == ["repeatmasker"]
    assert "BP3" in summary["criteria_not_evaluated"]
    assert summary["gnomad_region_flags_not_evaluated"] == {
        "lcr": ["1"],
        "segdup": ["1"],
    }
    assert (
        result.details["gnomad_region_flags_not_evaluated"]
        == summary["gnomad_region_flags_not_evaluated"]
    )
    assert 'GnomadRegionFlagsNotEvaluated="lcr:1;segdup:1"' in text
    assert "ACMG_GNOMAD_REGION=" not in text
    assert {"acmg_bp3_not_evaluated", "acmg_gnomad_region_flags_not_evaluated"} <= {
        w.code for w in result.warnings
    }
    assert {o.role for o in result.outputs} >= {"annotated_vcf", "acmg_summary"}
    assert "acmg_research_only" in {w.code for w in result.warnings}
    assert result.metrics["acmg_b"] == 1


@needs_fastvep
@pytest.mark.parametrize("previous_output", [False, True])
def test_1215_acmg_index_failure_does_not_publish_vcf(
    tmp_path, monkeypatch, previous_output
):
    inputs = _inputs(tmp_path)
    params = _params(tmp_path, _sa_dir(tmp_path), **inputs)
    vcf = tmp_path / "out" / "acmg_annotated.vcf.gz"
    index = Path(f"{vcf}.tbi")
    if previous_output:
        vcf.parent.mkdir()
        shutil.copy2(inputs["input"], vcf)
        shutil.copy2(Path(f"{inputs['input']}.tbi"), index)
        previous = (vcf.read_bytes(), index.read_bytes())

    tabix = get_tool_path("tabix")
    assert tabix is not None
    real_tabix = _tool_command_parts(tabix)
    fake = python_tool(
        tmp_path / "bin",
        "tabix",
        f"""import subprocess
import sys
if "-p" in sys.argv[1:]:
    sys.exit("simulated indexing failure")
sys.exit(subprocess.call([*{real_tabix!r}, *sys.argv[1:]]))
""",
    )
    with monkeypatch.context() as env:
        env.setenv("PATH", str(fake.parent), prepend=os.pathsep)
        clear_tool_caches()
        try:
            result = api.vcf.acmg(params, check=False)
        finally:
            clear_tool_caches()

    assert result.exit_code == 1
    assert result.error is not None
    assert "simulated indexing failure" in result.error.message
    if previous_output:
        assert (vcf.read_bytes(), index.read_bytes()) == previous
    else:
        assert not vcf.exists()
        assert not index.exists()
    assert not Path(f"{vcf}.csi").exists()


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


@needs_fastvep
def test_issue_629_packaged_fastvep_classifies_through_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path)
    sa = _sa_dir(tmp_path)
    outdir = tmp_path / "cli-out"
    main(
        ["vcf", "acmg", "--vcf-input", str(inputs["input"]),
         "--research-acmg", "--ref", str(inputs["ref"]),
         "--gff3", str(inputs["gff3"]), "--sa-dir", str(sa),
         "--outdir", str(outdir), "--threads", "2", "--json"]
    )  # fmt: skip
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["exit_code"] == 0
    assert "acmg_research_only" in {warning["code"] for warning in manifest["warnings"]}
    annotated = outdir / "acmg_annotated.vcf.gz"
    assert _acmg(annotated)[1017]["ACMG_CLASS"] == "LP"
    assert _acmg(annotated)[1150]["ACMG_CLASS"] == "B"
    assert Path(str(annotated) + ".tbi").is_file()


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
    params = _params(tmp_path, sa, site_data="library", **_inputs(tmp_path))
    result = api.vcf.acmg(params, check=False)
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
    params = _params(tmp_path, _sa_dir(tmp_path), **inputs)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        result = api.vcf.acmg(params, check=False)
        gc.collect()
    # #1473: stopping before setup completes must still close both stderr pipes.
    # #1517: collection can also finalize sockets left by other tests in this worker.
    assert not [
        w.message
        for w in caught
        if issubclass(w.category, ResourceWarning) and isinstance(w.source, IOBase)
    ]
    assert result.error is not None
    assert "fastVEP failed before reading the input" in result.error.message


def test_api_acmg_refuses_without_the_opt_in_before_making_the_outdir(tmp_path):
    # Review of #988: the API made the output folder before refusing.
    params = VcfAcmgParams(
        input=tmp_path / "in.vcf.gz", sa_dir=tmp_path, outdir=tmp_path / "out"
    )
    result = api.vcf.acmg(params, check=False)
    assert result.exit_code == 2
    assert result.error is not None and "--research-acmg" in result.error.message
    assert not (tmp_path / "out").exists()


def test_acmg_declares_the_gff3_folder_fastvep_writes_its_cache_in(tmp_path):
    # Review of #988: two server jobs could write the same transcript cache.
    given = VcfAcmgParams(gff3=tmp_path / "given" / "genes.gff3")
    assert tmp_path / "given" in set(given.write_folders())
    settings["acmg_gff3"] = str(tmp_path / "configured" / "genes.gff3")
    assert tmp_path / "configured" in set(VcfAcmgParams().write_folders())


@needs_fastvep
def test_acmg_refuses_a_chromosome_the_gnomad_database_lacks(tmp_path):
    # Review of #988: fastVEP's warning covers only autosomes and X, so a chrY
    # record was classified without gnomAD.
    text = (FIXTURES / "in_vep.vcf").read_text()
    text = text.replace(
        "##contig=<ID=1,length=248956422>\n",
        "##contig=<ID=1,length=248956422>\n##contig=<ID=Y,length=57227415>\n",
    )
    text += "Y\t2781480\t.\tC\tT\t50\tPASS\t.\tGT\t0/1\n"
    sa = _sa_dir(tmp_path)
    params = _params(tmp_path, sa, site_data="library", **_inputs(tmp_path, text))
    result = api.vcf.acmg(params, check=False)
    assert result.exit_code == 1
    assert result.error is not None
    assert "gnomAD database holds nothing of Y" in result.error.message
    assert not (tmp_path / "out" / "acmg_annotated.vcf.gz").exists()


@needs_fastvep
def test_acmg_leaves_chrm_unclassified_when_gnomad_lacks_it(tmp_path):
    # Code review of the #988 fix: refusing every contig gnomAD lacks refused
    # whole-genome VCFs for their chrM, alt and decoy records.
    text = (FIXTURES / "in_vep.vcf").read_text()
    text = text.replace(
        "##contig=<ID=1,length=248956422>\n",
        "##contig=<ID=1,length=248956422>\n##contig=<ID=MT,length=16569>\n",
    )
    text += "MT\t7000\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n"
    sa = _sa_dir(tmp_path)

    result = api.vcf.acmg(_params(tmp_path, sa, **_inputs(tmp_path, text)))

    acmg = _acmg(tmp_path / "out" / "acmg_annotated.vcf.gz")
    assert acmg[7000] == {}
    assert acmg[1150]["ACMG_CLASS"] == "B"
    assert "acmg_contigs_not_classified" in {w.code for w in result.warnings}
    summary = json.loads((tmp_path / "out" / "acmg_summary.json").read_text())
    assert summary["contigs_not_classified"] == ["MT"]


@needs_fastvep
def test_acmg_refuses_databases_of_another_assembly(tmp_path):
    # Review of #988: a whole GRCh37 set passed, as fastVEP only warns when
    # the databases disagree with each other.
    sa = _sa_dir(tmp_path, assembly="GRCh37")
    result = api.vcf.acmg(_params(tmp_path, sa, **_inputs(tmp_path)), check=False)
    assert result.exit_code == 1
    assert result.error is not None
    assert "gnomad.osa2 is built for GRCh37" in result.error.message
    assert not (tmp_path / "out" / "acmg_annotated.vcf.gz").exists()


@needs_fastvep
@pytest.mark.parametrize("explicit_outdir", [True, False])
def test_acmg_refuses_to_overwrite_an_input_named_like_its_output(
    tmp_path, explicit_outdir
):
    # Review of #988: the classified VCF replaced an input of the same name.
    inputs = _inputs(tmp_path)
    # Force a real collision, including the default for inputs already in WGSEv6/.
    destination = tmp_path if explicit_outdir else tmp_path / "WGSEv6"
    destination.mkdir(exist_ok=True)
    named = destination / "acmg_annotated.vcf.gz"
    inputs["input"].rename(named)
    Path(f"{inputs['input']}.tbi").rename(f"{named}.tbi")
    before = named.read_bytes()
    params = VcfAcmgParams(
        research_acmg=True,
        sa_dir=_sa_dir(tmp_path),
        input=named,
        ref=inputs["ref"],
        gff3=inputs["gff3"],
        outdir=destination if explicit_outdir else None,
    )
    result = api.vcf.acmg(params, check=False)
    assert result.error is not None and "would overwrite" in result.error.message
    assert named.read_bytes() == before


@needs_fastvep
def test_1087_acmg_default_output_preserves_same_named_lab_input(tmp_path):
    inputs = _inputs(tmp_path)
    named = tmp_path / "acmg_annotated.vcf.gz"
    inputs["input"].rename(named)
    Path(f"{inputs['input']}.tbi").rename(f"{named}.tbi")
    before = named.read_bytes()

    result = api.vcf.acmg(
        VcfAcmgParams(
            research_acmg=True,
            sa_dir=_sa_dir(tmp_path),
            input=named,
            ref=inputs["ref"],
            gff3=inputs["gff3"],
        )
    )

    annotated = Path(next(o.path for o in result.outputs if o.role == "annotated_vcf"))
    assert annotated == tmp_path / "WGSEv6" / named.name
    assert any(info.get("ACMG_CLASS") for info in _acmg(annotated).values())
    assert named.read_bytes() == before


@needs_fastvep
def test_acmg_replaces_the_classes_of_an_earlier_run(tmp_path):
    # Review of #988: classifying an acmg_annotated.vcf.gz again appended a
    # second ACMG_CLASS, and a record left unclassified kept its old one.
    first = tmp_path / "first"
    first.mkdir()
    sa = _sa_dir(first)
    api.vcf.acmg(_params(first, sa, **_inputs(first)))
    with gzip.open(first / "out" / "acmg_annotated.vcf.gz", "rt") as f:
        text = f.read()
    # A stale call on a reference-only record, which fastVEP leaves unclassified.
    text += "1\t2900\t.\tA\t.\t50\tPASS\tACMG_CLASS=P;ACMG_TRANSCRIPT=OLD\tGT\t0/0\n"

    api.vcf.acmg(_params(tmp_path, sa, **_inputs(tmp_path, text)))

    with gzip.open(tmp_path / "out" / "acmg_annotated.vcf.gz", "rt") as f:
        lines = f.read().splitlines()
    assert sum(line.startswith("##INFO=<ID=ACMG_CLASS,") for line in lines) == 1
    assert sum(line.startswith("##WGSE_ACMG=") for line in lines) == 1
    acmg = _acmg(tmp_path / "out" / "acmg_annotated.vcf.gz")
    assert acmg[1150]["ACMG_CLASS"] == "B"
    assert acmg[2900] == {}
    records = [line for line in lines if not line.startswith("#")]
    assert all(line.count("ACMG_CLASS=") <= 1 for line in records)


@needs_fastvep
def test_acmg_summary_counts_repeatmasker_and_bp3_when_loaded(tmp_path):
    # Review of #988: a loaded RepeatMasker track was reported unused, BP3
    # never evaluated, and the .osi left out of the database inventory.
    sa = _sa_dir(tmp_path, repeatmasker=True)
    api.vcf.acmg(_params(tmp_path, sa, **_inputs(tmp_path)))
    summary = json.loads((tmp_path / "out" / "acmg_summary.json").read_text())
    assert summary["sources_not_used"] == []
    assert "BP3" not in summary["criteria_not_evaluated"]
    assert "repeatmasker.osi" in {f["file"] for f in summary["databases"]["files"]}


@needs_fastvep
def test_acmg_reports_fastveps_error_when_it_stops_partway(tmp_path):
    # Review of #988: fastVEP stopping while bcftools still fed it was
    # reported as "bcftools failed reading the input" (bcftools's SIGPIPE).
    header = "".join(
        line
        for line in (FIXTURES / "in_vep.vcf").read_text().splitlines(keepends=True)
        if line.startswith("#")
    ).replace("#CHROM", '##INFO=<ID=NOTE,Number=1,Type=String,Description="">\n#CHROM')
    # Past fastVEP's first batch, with plenty left for bcftools to write.
    body = b"".join(
        f"1\t{pos}\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n".encode()
        for pos in range(1, 300_000)
    ).replace(
        b"\t50000\t.\tA\tG\t50\tPASS\t.", b"\t50000\t.\tA\tG\t50\tPASS\tNOTE=caf\xe9"
    )
    inputs = _inputs(tmp_path)
    plain = tmp_path / "latin1.vcf"
    plain.write_bytes(header.encode() + body)
    inputs["input"] = Path(bgzip_index(plain))
    result = api.vcf.acmg(_params(tmp_path, _sa_dir(tmp_path), **inputs), check=False)
    assert result.error is not None
    assert result.error.message.startswith("fastVEP failed: ")
    assert "UTF-8" in result.error.message
