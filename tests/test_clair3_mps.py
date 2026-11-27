import json
import os
import platform
import shutil
import sys
from types import SimpleNamespace

import pytest

from tests.fake_tools import python_tool
from tests.htslib_helpers import needs_tools, run_wgsextract, snp_sample, vcf_calls
from tests.test_caller_ploidy import sex_par as sex_par
from tests.test_caller_ploidy_review import _sample
from tests.test_clair3 import DORADO_RG, make_model
from tests.test_clair3 import clair3_gpu as clair3_gpu
from tests.test_clair3 import fake_clair3 as fake_clair3
from wgsextract_cli import api
from wgsextract_cli.api.vcf import VcfClair3Params
from wgsextract_cli.commands.vcf import clair3
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.utils import UsageError, WGSExtractError


@pytest.mark.parametrize(
    ("host", "machine", "error", "message"),
    [
        ("darwin", "x86_64", WGSExtractError, "Apple Silicon macOS only"),
        ("linux", "aarch64", UsageError, "x86-64 Linux with an NVIDIA GPU"),
    ],
)
def test_gpu_is_refused_where_clair3_has_no_gpu_build(
    monkeypatch, host, machine, error, message
):
    """#1101: MPS is Apple Silicon only; CUDA still needs x86-64 Linux."""
    monkeypatch.setattr(sys, "platform", host)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    with pytest.raises(error, match=message):
        run_wgsextract("vcf", "clair3", "--input", "reads.bam", "--gpu")


@pytest.fixture
def mps_clair3(tmp_path, monkeypatch, fake_clair3):
    """The slow caller/device boundary; samtools and bcftools remain real."""
    monkeypatch.setattr(clair3, "verify_gpu", clair3.verify_mps)
    state = tmp_path / "device.json"
    state.write_text(
        json.dumps(
            {
                "package": "wgse-clair3",
                "version": "0.1.0",
                "clair3_version": "2.0.5",
                "streaming_mps": 1,
                "mps_available": True,
            }
        )
    )
    tool = fake_clair3.models.parent / "wgse-clair3"
    python_tool(
        tool.parent,
        tool.name,
        f"""
import os, sys
from pathlib import Path
if sys.argv[1:] == ["--wgse-device-check"]:
    text = Path({str(state)!r}).read_text()
    if text == "unusable":
        raise SystemExit(4)
    print(text)
else:
    os.execv({str(tool.parent / "run_clair3.sh")!r},
             [{str(tool.parent / "run_clair3.sh")!r}, *sys.argv[1:]])
""",
    )
    clear_tool_caches()
    return SimpleNamespace(tool=tool, state=state, caller=fake_clair3)


@pytest.mark.parametrize(
    ("state", "message", "code", "kind"),
    [
        (None, "WGSE-built wgse-clair3 package", 3, "missing_dependency"),
        (
            {"package": "unpatched"},
            "WGSE-built wgse-clair3 package",
            3,
            "missing_dependency",
        ),
        ("invalid JSON", "Couldn't check Clair3's MPS build", 3, "missing_dependency"),
        ("unusable", "Couldn't check Clair3's MPS build", 3, "missing_dependency"),
        (
            {
                "package": "wgse-clair3",
                "clair3_version": "2.0.5",
                "streaming_mps": 1,
                "mps_available": False,
            },
            "PyTorch MPS is unavailable",
            1,
            "failure",
        ),
    ],
)
@needs_tools("samtools", "bcftools", "tabix")
def test_1101_mps_refuses_before_work_when_device_or_patch_missing(
    tmp_path, mps_clair3, state, message, code, kind, request, monkeypatch
):
    reads = snp_sample(tmp_path, DORADO_RG)
    if state is None:
        mps_clair3.tool.unlink()
        # Also hide a developer's real optional Pixi installation.
        path = os.environ["PATH"]
        request.getfixturevalue("fake_pixi")
        monkeypatch.setenv("PATH", path)
    else:
        mps_clair3.state.write_text(
            state if isinstance(state, str) else json.dumps(state)
        )
    out = tmp_path / "not-created"
    result = api.vcf.clair3(
        VcfClair3Params(
            input=reads.bam, ref=reads.ref, gpu=True, outdir=out, no_preflight=True
        ),
        check=False,
    )
    assert result.exit_code == code
    assert result.error is not None
    assert result.error.kind == kind
    assert message in result.error.message
    assert not list(out.glob("*.vcf.gz"))
    assert not mps_clair3.caller.runs()


@needs_tools("samtools", "bcftools", "tabix")
@pytest.mark.parametrize("evidence", ["none", "gvcf"])
def test_1101_mps_calls_use_streaming_not_cuda_tensors(tmp_path, mps_clair3, evidence):
    reads = snp_sample(tmp_path, DORADO_RG)
    model = make_model(tmp_path, "my_r1041_model")
    out = tmp_path / "mps calls"
    run_wgsextract(
        "vcf",
        "clair3",
        "--input",
        reads.bam,
        "--ref",
        reads.ref,
        "--gpu",
        "--model",
        str(model),
        "--threads",
        "2",
        "--region",
        "chr1:900-1100",
        "--outdir",
        str(out),
        "--reference-evidence",
        evidence,
    )
    assert vcf_calls(out / "clair3.vcf.gz") == reads.calls[:1]
    if evidence == "gvcf":
        assert (out / "clair3.g.vcf.gz").is_file()
        assert (out / "clair3.g.vcf.gz.tbi").is_file()
    (run,) = mps_clair3.caller.runs()
    # contract: MPS must retain CPU streaming/scaling, not CUDA's disk tensors.
    assert (run["opts"]["device"], run["opts"]["use_gpu"]) == ("mps", "False")
    assert run["model"] == str(model.resolve())


@needs_tools("samtools", "bcftools", "tabix")
@pytest.mark.parametrize("evidence", ["none", "gvcf"])
@pytest.mark.parametrize("sex", ["Male", "Female"])
def test_1106_mps_sex_partitions_handle_haploid_and_excluded_intervals(
    sex_par, mps_clair3, evidence, sex
):
    sample = _sample(
        sex_par.root / "mps-sex", ("chr20", "chrX", "chrY"), sex_par.ref.stem
    )
    run_wgsextract("sample", "set", "--input", str(sample.bam), "--sex", sex)
    model = make_model(sex_par.root, "custom")
    out = sex_par.root / "mps-sex-calls"
    result = api.vcf.clair3(
        VcfClair3Params(
            input=sample.bam,
            ref=sample.ref,
            outdir=out,
            model=str(model),
            gpu=True,
            threads=1,
            reference_evidence=evidence,
        )
    )
    assert result.status == "ok", result.error
    observed = {row[0]: row[-1] for row in vcf_calls(out / "clair3.vcf.gz")}
    assert observed == (
        {"chr20": "1/1", "chrX": "1", "chrY": "1"}
        if sex == "Male"
        else {"chr20": "1/1", "chrX": "1/1"}
    )
    if evidence == "gvcf":
        import gzip

        with gzip.open(out / "clair3.g.vcf.gz", "rt") as source:
            contigs = {
                line.split("\t")[0] for line in source if not line.startswith("#")
            }
        assert contigs == set(observed)


@needs_tools("samtools", "bcftools", "tabix")
def test_1106_cuda_still_rejects_haploid_sex_partitions(sex_par, clair3_gpu):
    run_wgsextract("sample", "set", "--input", str(sex_par.bam), "--sex", "Male")
    result = api.vcf.clair3(
        VcfClair3Params(
            input=sex_par.bam,
            ref=sex_par.ref,
            outdir=sex_par.root / "cuda-calls",
            gpu=True,
            threads=1,
        ),
        check=False,
    )
    assert result.error is not None
    assert "does not support this sex/PAR ploidy policy" in result.error.message
    assert not clair3_gpu.clair3.runs()


@needs_tools("samtools", "bcftools", "tabix")
@pytest.mark.parametrize("choice", [None, "custom"])
def test_1106_mps_models_and_cram_version_ignore_cpu_launcher(
    tmp_path, mps_clair3, monkeypatch, choice
):
    reads = snp_sample(tmp_path, DORADO_RG)
    selected_bin = tmp_path / "selected" / "bin"
    selected_bin.mkdir(parents=True)
    moved = selected_bin / "wgse-clair3"
    shutil.move(mps_clair3.tool, moved)
    monkeypatch.setenv("PATH", f"{selected_bin}{os.pathsep}{os.environ['PATH']}")
    model = make_model(selected_bin / "models", choice or "r1041_e82_400bps_sup_v500")
    actual = mps_clair3.tool.parent / "actual-caller"
    shutil.move(mps_clair3.tool.parent / "run_clair3.sh", actual)
    python_tool(
        selected_bin,
        "wgse-clair3",
        f"""
import os, sys
from pathlib import Path
if sys.argv[1:] == ["--wgse-device-check"]:
    print(Path({str(mps_clair3.state)!r}).read_text())
else:
    os.execv({str(actual)!r}, [{str(actual)!r}, *sys.argv[1:]])
""",
    )
    # A foreign CPU launcher is deliberately old. It must not force CRAM staging.
    python_tool(mps_clair3.tool.parent, "run_clair3.sh", 'print("Clair3 v2.0.4")')
    clear_tool_caches()
    result = api.vcf.clair3(
        VcfClair3Params(
            input=reads.cram,
            ref=reads.ref,
            model=choice,
            gpu=True,
            outdir=tmp_path / "selected-calls",
            threads=1,
        ),
        check=False,
    )
    if choice is None:
        assert result.error is not None
        assert str(model) in result.error.message
        assert "pinned SHA-256" in result.error.message
    else:
        assert result.status == "ok", result.error
        (run,) = mps_clair3.caller.runs()
        assert run["model"] == str(model.resolve())
        assert run["cram_version"] == [3, 1]
