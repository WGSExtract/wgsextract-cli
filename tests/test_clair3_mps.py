import json
import os
import platform
import sys
from types import SimpleNamespace

import pytest

from tests.fake_tools import python_tool
from tests.htslib_helpers import needs_tools, run_wgsextract, snp_sample, vcf_calls
from tests.test_clair3 import DORADO_RG, make_model
from tests.test_clair3 import fake_clair3 as fake_clair3
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
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
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
    print(Path({str(state)!r}).read_text())
else:
    os.execv({str(tool.parent / "run_clair3.sh")!r},
             [{str(tool.parent / "run_clair3.sh")!r}, *sys.argv[1:]])
""",
    )
    clear_tool_caches()
    return SimpleNamespace(tool=tool, state=state, caller=fake_clair3)


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (None, "WGSE-built wgse-clair3 package"),
        ({"package": "unpatched"}, "WGSE-built wgse-clair3 package"),
        (
            {
                "package": "wgse-clair3",
                "clair3_version": "2.0.5",
                "streaming_mps": 1,
                "mps_available": False,
            },
            "PyTorch MPS is unavailable",
        ),
    ],
)
def test_1101_mps_refuses_before_work_when_device_or_patch_missing(
    tmp_path, mps_clair3, state, message, request, monkeypatch
):
    if state is None:
        mps_clair3.tool.unlink()
        # Also hide a developer's real optional Pixi installation.
        path = os.environ["PATH"]
        request.getfixturevalue("fake_pixi")
        monkeypatch.setenv("PATH", path)
    else:
        mps_clair3.state.write_text(json.dumps(state))
    out = tmp_path / "not-created"
    with pytest.raises(WGSExtractError, match=message):
        run_wgsextract(
            "vcf",
            "clair3",
            "--gpu",
            "--input",
            "not-read.bam",
            "--outdir",
            str(out),
            "--no-preflight",
        )
    assert not out.exists()
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
