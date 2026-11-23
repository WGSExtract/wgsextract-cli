"""Paraphase boundary tests (#1285) with real indexed BAM/CRAM input."""

import csv
import json
import os
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tests.fake_tools import FakePixi, python_tool
from tests.htslib_helpers import needs_tools, sam_to_bam
from wgsextract_cli.api import TypingParaphaseParams, genotyping
from wgsextract_cli.core.results import CommandResult

pytestmark = [
    needs_tools("samtools"),
    pytest.mark.skipif(
        os.name == "nt", reason="No Paraphase Pixi environment on Windows"
    ),
]
SAMTOOLS = shutil.which("samtools")
# Selected fields of Paraphase 3.3.1's public GIAB HG002 Revio 48x trial.
HG002 = Path(__file__).with_name("fixtures") / "paraphase_hg002.json"

# Only the external caller is substituted. Use samtools to open the indexed
# alignment the wrapper actually supplied, and retain valid BAM/VCF outputs.
FAKE_ENGINE = """
import argparse, json, pathlib, shutil, subprocess

p = argparse.ArgumentParser()
p.add_argument("--version", action="version", version={version!r})
for flag in ("-b", "-r", "-o", "-p", "-t", "--genome", "-g", "--samtools", "--minimap2"):
    p.add_argument(flag)
a = p.parse_args()
samtools = a.samtools or {samtools!r}
subprocess.run(
    [samtools, "view", "-c", "-T", a.r, a.b, "chr1:1-300"], check=True,
)
out = pathlib.Path(a.o)
out.mkdir()
prefix = out / (a.p + ".paraphase")
reports = json.loads(pathlib.Path({fixture!r}).read_text())
if a.g:
    reports = {{gene: reports[gene] for gene in a.g.split(",")}}
pathlib.Path(str(prefix) + ".json").write_text(json.dumps(reports))
bam = pathlib.Path(str(prefix) + ".bam")
subprocess.run([samtools, "view", "-b", "-T", a.r, "-o", bam, a.b], check=True)
subprocess.run([samtools, "index", bam], check=True)
vcfs = out / (a.p + "_paraphase_vcfs")
vcfs.mkdir()
for gene in reports:
    (vcfs / (a.p + "_" + gene + ".vcf")).write_text(
        "##fileformat=VCFv4.2\\n#CHROM\\tPOS\\tID\\tREF\\tALT\\tQUAL\\tFILTER\\tINFO\\n"
    )
"""


@pytest.fixture
def paraphase_env(fake_pixi: FakePixi) -> Path:
    assert SAMTOOLS is not None
    (fake_pixi.host_bin / "samtools").symlink_to(SAMTOOLS)
    prefix = fake_pixi.env("paraphase", locked=("paraphase",))
    bindir = prefix / "bin"
    for tool in ("samtools", "minimap2"):
        (bindir / tool).symlink_to(SAMTOOLS)
    python_tool(
        bindir, "paraphase",
        FAKE_ENGINE.format(version="3.3.1", samtools=SAMTOOLS, fixture=str(HG002)),
    )  # fmt: skip
    return prefix


def _alignment(
    folder: Path,
    platforms: tuple[str, ...],
    *,
    chr1_length: int = 248_956_422,
    decoy: bool = False,
    read_group: str | None = "rg0",
    chr1_md5: str | None = None,
) -> tuple[Path, Path]:
    # Only the tiny prefix of chr1 is read by this fixture. A sparse FASTA
    # lets its index declare the real assembly length without writing 249 MB.
    ref = folder / "reference with spaces.fa"
    with ref.open("wb") as stream:
        stream.write(b">chr1\n" + b"A" * 300)
        stream.truncate(6 + chr1_length)
    Path(f"{ref}.fai").write_text(
        f"chr1\t{chr1_length}\t6\t{chr1_length}\t{chr1_length}\n"
    )
    header = ["@HD\tVN:1.6\tSO:coordinate", f"@SQ\tSN:chr1\tLN:{chr1_length}"]
    if chr1_md5:
        header[1] += f"\tM5:{chr1_md5}"
    if decoy:
        header.append("@SQ\tSN:MAP2K3_chr17_22578583_22605165\tLN:26582")
    for i, platform in enumerate(platforms):
        header.append(
            f"@RG\tID:rg{i}\tSM:example" + (f"\tPL:{platform}" if platform else "")
        )
    read = "read1\t0\tchr1\t101\t60\t50M\t*\t0\t0\t" + "A" * 50 + "\t" + "I" * 50
    if read_group is not None:
        read += f"\tRG:Z:{read_group}"
    bam = Path(sam_to_bam([*header, read], folder / "sample with spaces.bam"))
    return bam, ref


def _run(
    bam: Path,
    ref: Path,
    outdir: Path,
    *,
    genes: str = "SMN1,PMS2,STRC",
    executable: Path | None = None,
) -> CommandResult:
    return genotyping.paraphase(
        TypingParaphaseParams(
            input=bam, ref=ref, genes=genes, threads=1, outdir=outdir,
            pacbio_licensed_executable=executable,
        ),
        check=False,
    )  # fmt: skip


@pytest.mark.parametrize(("platform", "cram"), [("PACBIO", False), ("ONT", True)])
def test_paraphase_preserves_gene_counts_and_null_strc(
    paraphase_env: Path,
    tmp_path: Path,
    platform: str,
    cram: bool,
) -> None:
    """#1285: SMN gene counts are calls; a null STRC CN is not a deletion."""
    bam, ref = _alignment(tmp_path, (platform,), decoy=not cram)
    if cram:
        assert SAMTOOLS is not None
        src = bam
        bam = src.with_suffix(".cram")
        # A tiny CRAM with data embedded avoids hashing the chromosome fixture.
        subprocess.run(
            [SAMTOOLS, "view", "-C", "--output-fmt-option", "no_ref", "-o", bam, src],
            check=True,
        )
        subprocess.run([SAMTOOLS, "index", bam], check=True)
    result = _run(bam, ref, tmp_path / "output with spaces", genes="SMN1,PMS2,STRC,HBA")
    assert result.status == "ok", result.error
    artifacts = {f.role: Path(f.path) for f in result.outputs}
    calls = json.loads(artifacts["paraphase_calls_json"].read_text())
    schema = files("wgsextract_cli").joinpath(
        "assets/schemas/wgsextract.paraphase.1.schema.json"
    )
    Draft202012Validator(json.loads(schema.read_text())).validate(calls)
    by_gene = {c["gene"]: c for c in calls["loci"]}
    assert by_gene["smn1"]["call_status"] == "called"
    assert by_gene["smn1"]["gene_fields"] == {
        "smn1_cn": 2,
        "smn2_cn": 2,
        "smn_del78_cn": 0,
    }
    assert by_gene["pms2"]["total_cn"] == 4
    assert by_gene["pms2"]["gene_fields"]["gene_cn"] == 2
    assert by_gene["strc"]["total_cn"] is None
    assert by_gene["strc"]["call_status"] == "no_call"
    assert by_gene["hba"]["gene_fields"] == {"genotype": "aa/aa", "sv_called": {}}
    with artifacts["paraphase_calls_tsv"].open(newline="") as stream:
        rows = {r["gene"]: r for r in csv.DictReader(stream, delimiter="\t")}
    assert rows["strc"]["total_cn"] == ""
    assert rows["strc"]["call_status"] == "no_call"
    assert json.loads(rows["smn1"]["gene_fields"])["smn2_cn"] == 2
    assert (
        json.loads(artifacts["paraphase_raw_json"].read_text())["smn1"]["smn1_cn"] == 2
    )
    assert artifacts["paraphase_bam_index"].is_file()
    assert sorted(
        Path(f.path).name.rsplit(".", 2)[-2]
        for f in result.outputs
        if f.role == "paraphase_vcf"
    ) == ["hba", "pms2", "smn1", "strc"]
    assert result.manifest is not None
    manifest = json.loads(Path(result.manifest).read_text())
    experimental = platform == "ONT"
    assert calls["platform"] == platform.lower()
    assert by_gene["smn1"]["experimental"] is experimental
    assert manifest["metrics"]["paraphase_experimental"] is experimental
    assert manifest["metrics"]["paraphase_engine_version"] == "3.3.1"
    assert manifest["metrics"]["paraphase_engine_license"] == "BSD-3-Clause-Clear"
    warning_codes = {warning["code"] for warning in manifest["warnings"]}
    assert ("paraphase_ont_experimental" in warning_codes) == experimental


@pytest.mark.parametrize("platforms", [("ONT",), ("PACBIO", "ONT"), ("PACBIO", ""), ()])
def test_pacbio_licensed_engine_is_not_even_probed_for_unqualified_read_groups(
    tmp_path: Path,
    platforms: tuple[str, ...],
) -> None:
    """#1285: the SLA is not for ONT, mixed input or a missing @RG PL."""
    bam, ref = _alignment(tmp_path, platforms)
    executed = tmp_path / "executed"
    executable = python_tool(
        tmp_path,
        "licensed",
        f"from pathlib import Path\nPath({str(executed)!r}).touch()\n",
    )
    result = _run(bam, ref, tmp_path / "out", executable=executable)
    assert result.exit_code == 2
    assert result.error is not None
    assert "PacBio Software License Agreement" in result.error.message
    assert "PL:PACBIO" in result.error.message
    assert not executed.exists()


def test_opt_in_records_licensed_engine_version(tmp_path: Path) -> None:
    """#1285: legitimate PacBio input can select the separate licensed engine."""
    bam, ref = _alignment(tmp_path, ("PACBIO", "PACBIO"))
    executable = python_tool(
        tmp_path, "licensed",
        FAKE_ENGINE.format(version="paraphase 4.1.0", samtools=SAMTOOLS, fixture=str(HG002)),
    )  # fmt: skip
    result = _run(bam, ref, tmp_path / "out", executable=executable)
    assert result.status == "ok", result.error
    assert result.metrics["paraphase_engine_version"] == "4.1.0"
    assert (
        result.metrics["paraphase_engine_license"]
        == "PacBio Software License Agreement"
    )


@pytest.mark.parametrize("read_group", [None, "not-declared"])
def test_pacbio_header_is_not_enough_when_read_group_is_unverified(
    tmp_path: Path,
    read_group: str | None,
) -> None:
    """#1285: a PacBio-only header does not make an unassigned read PacBio."""
    bam, ref = _alignment(tmp_path, ("PACBIO",), read_group=read_group)
    executed = tmp_path / "executed"
    executable = python_tool(
        tmp_path,
        "licensed",
        f"from pathlib import Path\nPath({str(executed)!r}).touch()\n",
    )
    result = _run(bam, ref, tmp_path / "out", executable=executable)
    assert result.exit_code == 2
    assert result.error is not None
    assert "lack a declared PL:PACBIO read group" in result.error.message
    assert not executed.exists()


def test_paraphase_refuses_same_length_reference_with_different_md5(
    paraphase_env: Path, tmp_path: Path
) -> None:
    """#1285: matching contig lengths alone do not ensure the bases match."""
    bam, ref = _alignment(tmp_path, ("PACBIO",), chr1_md5="a" * 32)
    ref.with_suffix(".dict").write_text(
        "@SQ\tSN:chr1\tLN:248956422\tM5:" + "b" * 32 + "\n"
    )
    result = _run(bam, ref, tmp_path / "out")
    assert result.status == "failed"
    assert result.error is not None
    assert "chr1: sequence MD5 differs" in result.error.message


def test_missing_paraphase_environment_does_not_install(
    fake_pixi: FakePixi, tmp_path: Path
) -> None:
    """#1285: no implicit package install, or fallback to another Paraphase."""
    assert SAMTOOLS is not None
    (fake_pixi.host_bin / "samtools").symlink_to(SAMTOOLS)
    fake_pixi.env("paraphase", locked=("paraphase",), installed=False)
    bam, ref = _alignment(tmp_path, ("PACBIO",))
    result = _run(bam, ref, tmp_path / "out")
    assert result.exit_code == 3
    assert result.error is not None
    assert "pixi install --locked -e paraphase" in result.error.message
    assert not fake_pixi.installed("paraphase")


def test_chm13_rejects_locus_not_in_pinned_catalog(
    paraphase_env: Path, tmp_path: Path
) -> None:
    """#1285: PMS2 isn't available on CHM13 in Paraphase 3.3.1."""
    bam, ref = _alignment(tmp_path, ("PACBIO",), chr1_length=248_387_328)
    result = _run(bam, ref, tmp_path / "out", genes="SMN1,PMS2")
    assert result.exit_code == 2
    assert result.error is not None
    assert "chm13" in result.error.message and "PMS2" in result.error.message
