"""Optional installed-package integration: only the MPS boundary is redirected."""

import gzip
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fake_tools import python_tool
from tests.htslib_helpers import run_wgsextract, vcf_calls
from wgsextract_cli import api
from wgsextract_cli.api.vcf import VcfClair3Params
from wgsextract_cli.core.dependencies import clear_tool_caches

ROOT = Path(__file__).resolve().parents[1]
PREFIX = ROOT / ".pixi/envs/clair3"
LAUNCHER = PREFIX / "bin/wgse-clair3"
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not LAUNCHER.is_file(),
    reason="requires the optional osx-arm64 wgse-clair3 package",
)


@pytest.mark.parametrize("sex", ["Male", "Female"])
@pytest.mark.parametrize("evidence", ["none", "gvcf"])
def test_1106_packaged_mps_handles_haploid_and_excluded_sex_chromosomes(
    tmp_path, monkeypatch, sex, evidence
):
    work = tmp_path / "synthetic"
    subprocess.run(
        [
            str(PREFIX / "bin/python"),
            str(ROOT / "packaging/wgse-clair3/test_streaming.py"),
            "--prepare-only",
            str(work),
        ],
        env={**os.environ, "PATH": f"{PREFIX / 'bin'}{os.pathsep}{os.environ['PATH']}"},
        check=True,
    )
    tools = tmp_path / "gpu-boundary"
    python_tool(
        tools,
        "wgse-clair3",
        f"""
import os, sys
os.execve({str(LAUNCHER)!r}, [{str(LAUNCHER)!r}, *sys.argv[1:]],
          {{**os.environ, "PYTHONPATH": {str(work / "gpu-boundary")!r}}})
""",
    )
    monkeypatch.setenv(
        "PATH", f"{tools}{os.pathsep}{PREFIX / 'bin'}{os.pathsep}{os.environ['PATH']}"
    )
    clear_tool_caches()
    reads = work / "reads.bam"
    run_wgsextract("sample", "set", "--input", str(reads), "--sex", sex)
    output = tmp_path / "calls"
    result = api.vcf.clair3(
        VcfClair3Params(
            input=reads,
            ref=work / "hg38.fa",
            model=str(work / "model"),
            gpu=True,
            threads=1,
            outdir=output,
            reference_evidence=evidence,
        )
    )
    assert result.status == "ok", result.error
    expected = (
        {"chr20": "1/1", "chrX": "1", "chrY": "1"}
        if sex == "Male"
        else {"chr20": "1/1", "chrX": "1/1"}
    )
    assert {row[0]: row[-1] for row in vcf_calls(output / "clair3.vcf.gz")} == expected
    if evidence == "gvcf":
        with gzip.open(output / "clair3.g.vcf.gz", "rt") as source:
            records = [line.split("\t") for line in source if not line.startswith("#")]
        assert {row[0] for row in records} == set(expected)
        assert any(row[4] == "<NON_REF>" for row in records)
        for row in records:
            genotype = row[9].split(":")[0]
            assert ("/" in genotype) == (expected[row[0]] == "1/1")
