"""`deps ready` for the caller families (#1183, offline readiness): Clair3's
and DeepVariant's backends, images, GPUs and model weights, with Docker,
nvidia-smi and the callers' releases faked at their boundaries. Readiness
inspects local images and never pulls one."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.fake_tools import fake_image_torch, set_gpus
from tests.htslib_helpers import needs_tools, snp_sample
from tests.test_clair3 import (
    DORADO_RG,
    make_model,
)
from tests.test_clair3 import (
    clair3_gpu as clair3_gpu,
)
from tests.test_clair3 import (
    fake_clair3 as fake_clair3,
)
from tests.test_deepvariant import (
    apple as apple,
)
from tests.test_deepvariant import (
    dv as dv,
)
from tests.test_deepvariant import (
    linux_x86 as linux_x86,
)
from tests.test_deepvariant import (
    mac_native as mac_native,
)
from tests.test_deps_ready import item, ready
from tests.test_deps_ready import offline as offline
from wgsextract_cli.commands.vcf import deepvariant
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.main import main

PINNED_MODEL = "r1041_e82_400bps_sup_v500"


def no_pulls(docker) -> None:
    """Docker was asked for nothing it would fetch from a registry."""
    commands = docker.runs(None)
    assert not [c for c in commands if c["cmd"] == "pull"]
    assert all(c["pull"] == "--pull=never" for c in commands if c["cmd"] == "run")


@pytest.fixture
def ont(tmp_path):
    (tmp_path / "ont").mkdir()
    return snp_sample(tmp_path / "ont", DORADO_RG)


# Clair3 ----------------------------------------------------------------------


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_a_clair3_model_that_fails_its_digest_blocks_the_run(
    offline, linux_x86, fake_clair3, ont, tmp_path, capsys
):
    model = make_model(tmp_path / "models", PINNED_MODEL, b"not the published weights")

    code, details = ready(
        capsys, "vcf", "clair3", "--input", ont.bam, "--ref", ont.ref,
        "--model", str(model),
    )  # fmt: skip

    found = item(details, "model")
    assert (code, found["status"], found["blocking"]) == (3, "incompatible", True)
    evidence = found["evidence"]
    assert evidence["verified"] is False
    assert (
        evidence["sha256"]["pileup.pt"]
        == hashlib.sha256(b"not the published weights").hexdigest()
    )
    assert evidence["terms"]["status"] == "unknown"
    assert evidence["source"] in found["fix_text"]
    assert item(details, "tool", "run_clair3.sh")["version"] == "Clair3 v2.0.5"


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_an_unpinned_clair3_model_is_ready_but_unverified(
    offline, linux_x86, fake_clair3, ont, tmp_path, capsys
):
    model = make_model(tmp_path / "models", "my_r1041_model")

    code, details = ready(
        capsys, "vcf", "clair3", "--input", ont.bam, "--ref", ont.ref,
        "--model", str(model),
    )  # fmt: skip

    assert (code, details["status"]) == (0, "ready_unverified")
    found = item(details, "model")
    assert found["status"] == "unverified"
    assert found["evidence"]["terms"]["status"] == "unknown"


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_clair3_gpu_records_image_driver_and_devices_without_pulling(
    offline, clair3_gpu, tmp_path, capsys
):
    reads = clair3_gpu.reads
    model = make_model(tmp_path / "models", "my_r1041_model")

    code, details = ready(
        capsys, "vcf", "clair3", "--input", reads.bam, "--ref", reads.ref,
        "--gpu", "--model", str(model),
    )  # fmt: skip

    assert (code, details["status"]) == (0, "ready_unverified")
    assert item(details, "backend")["status"] == "runnable"
    image = item(details, "image")
    assert (image["status"], image["evidence"]["image_id"]) == ("runnable", "sha256:1")
    gpu = item(details, "gpu")
    assert (gpu["status"], gpu["version"]) == ("runnable", "550.54.14")
    assert gpu["evidence"]["devices"] == [0]
    no_pulls(clair3_gpu.docker)


def _no_docker(gpu) -> None:
    """No docker anywhere on PATH (nvidia-smi stays)."""
    (gpu.docker.root.parent / "bin" / "docker").unlink()
    os.environ["PATH"] = os.pathsep.join(
        folder
        for folder in os.environ["PATH"].split(os.pathsep)
        if not (Path(folder) / "docker").exists()
    )
    clear_tool_caches()


@needs_tools("samtools", "bcftools", "tabix")
@pytest.mark.parametrize(
    ("setup", "backend", "message"),
    [
        pytest.param(lambda gpu: gpu.docker.set(images={}), "missing",
                     "docker pull hkubal/clair3", id="no-local-image"),
        pytest.param(_no_docker, "missing", "Docker not found", id="no-docker"),
        pytest.param(lambda gpu: fake_image_torch(gpu.image_bin, cuda=False),
                     "incompatible", "can't use the GPU", id="driver-too-old"),
        pytest.param(lambda gpu: set_gpus(gpu.gpus, error="NVML: Driver/library version mismatch"),
                     "missing", "NVIDIA driver isn't working", id="broken-driver"),
    ],
)  # fmt: skip
def test_1183_clair3_gpu_names_what_the_host_lacks_and_pulls_nothing(
    offline, clair3_gpu, tmp_path, capsys, monkeypatch, setup, backend, message
):
    monkeypatch.setenv("PATH", os.environ["PATH"])  # restored after the test
    setup(clair3_gpu)
    reads = clair3_gpu.reads

    code, details = ready(
        capsys, "vcf", "clair3", "--input", reads.bam, "--ref", reads.ref,
        "--gpu", "--model", str(make_model(tmp_path / "models", "my_r1041_model")),
    )  # fmt: skip

    found = item(details, "backend")
    assert (code, found["status"], found["blocking"]) == (3, backend, True)
    assert message in found["detail"]
    if (clair3_gpu.docker.root.parent / "bin" / "docker").exists():
        no_pulls(clair3_gpu.docker)


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_a_missing_local_image_names_the_explicit_pull(
    offline, clair3_gpu, tmp_path, capsys
):
    clair3_gpu.docker.set(images={})
    reads = clair3_gpu.reads

    _, details = ready(
        capsys, "vcf", "clair3", "--input", reads.bam, "--ref", reads.ref, "--gpu",
    )  # fmt: skip

    image = item(details, "image")
    assert (image["status"], image["blocking"]) == ("missing", True)
    assert image["fix_text"].startswith("Download it explicitly: docker pull")
    no_pulls(clair3_gpu.docker)


# DeepVariant -----------------------------------------------------------------


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_wgse_deepvariant_weights_are_checked_against_their_digests(
    offline, mac_native, capsys
):
    sample, config = mac_native.sample, mac_native.tmp / "config.toml"
    config.write_text(f"deepvariant_weights = {json.dumps(str(mac_native.weights))}\n")
    command = (
        "--config", str(config),
        "vcf", "deepvariant", "--input", sample.bam, "--ref", sample.ref,
    )  # fmt: skip

    code, details = ready(capsys, *command)

    backend = item(details, "backend")
    assert details["blocking"] == []
    assert (code, backend["name"], backend["status"]) == (
        0,
        "wgse-deepvariant",
        "runnable",
    )
    weights = item(details, "model")
    assert (weights["status"], weights["evidence"]["verified"]) == ("runnable", True)
    assert weights["evidence"]["terms"]["status"] == "unknown"
    assert backend["evidence"]["model_type"] == "WGS"

    (mac_native.weights / "wgs.dvw").write_bytes(b"other weights")
    code, details = ready(capsys, *command)
    weights = item(details, "model")
    assert (code, weights["status"], weights["blocking"]) == (3, "incompatible", True)


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_googles_image_is_identified_in_the_local_engine(offline, dv, capsys):
    dv.docker.add_image(
        deepvariant.DEFAULT_IMAGE,
        image_id="sha256:dv",
        repo_digests=("google/deepvariant@sha256:abc",),
        labels={"org.opencontainers.image.licenses": "BSD-3-Clause"},
    )

    code, details = ready(
        capsys, "vcf", "deepvariant", "--input", dv.sample.bam, "--ref", dv.sample.ref
    )

    assert (code, item(details, "backend")["status"]) == (0, "runnable")
    evidence = item(details, "image")["evidence"]
    assert evidence["image_id"] == "sha256:dv"
    assert evidence["repo_digests"] == ["google/deepvariant@sha256:abc"]
    assert evidence["terms"] == {
        "status": "recorded",
        "licence": "BSD-3-Clause",
        "source": "image label org.opencontainers.image.licenses",
    }
    no_pulls(dv.docker)


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_apple_silicon_without_the_native_image_names_its_local_build(
    offline, apple, capsys
):
    code, details = ready(
        capsys, "vcf", "deepvariant", "--input", apple.sample.bam,
        "--ref", apple.sample.ref,
    )  # fmt: skip

    image = item(details, "image")
    assert (code, image["status"]) == (3, "missing")
    assert "packaging/deepvariant-arm64/build.sh" in image["fix_text"]
    no_pulls(apple.docker)


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_deepvariant_gpu_is_unsupported_on_a_mac(offline, apple, capsys):
    code, details = ready(
        capsys, "vcf", "deepvariant", "--input", apple.sample.bam,
        "--ref", apple.sample.ref, "--gpu",
    )  # fmt: skip

    backend = item(details, "backend")
    assert (code, backend["status"]) == (3, "unsupported")
    assert "--gpu is not supported on this platform" in backend["detail"]


# Copilot review round 2 on PR #1371 ------------------------------------------


def test_1183_a_remote_clair3_input_is_not_probed_and_leaks_no_credentials(
    offline, linux_x86, fake_clair3, capsys
):
    """Typed as a Path, the URL lost a slash of its scheme (https:/...),
    which the URL redactor didn't recognise in samtools' failure log."""
    url = "https://reader:hunter2@example.org/ont.bam?X-Amz-Signature=abc123"
    capsys.readouterr()

    with pytest.raises(SystemExit) as exit_:
        main(
            [
                "--json",
                "--debug",
                "deps",
                "ready",
                "--",
                "vcf",
                "clair3",
                "--input",
                url,
            ]
        )

    out, err = capsys.readouterr()
    assert exit_.value.code == 3
    assert item(json.loads(out)["details"], "input")["status"] == "unsupported"
    assert "hunter2" not in out + err and "abc123" not in out + err


@needs_tools("samtools", "bcftools", "tabix")
def test_1183_a_deepvariant_checkpoint_prefix_is_a_model_not_a_missing_file(
    offline, dv, capsys
):
    """model.ckpt names model.ckpt.index and model.ckpt.data-*, not a file."""
    checkpoint = dv.tmp / "custom" / "model.ckpt"
    checkpoint.parent.mkdir()
    for suffix in (".index", ".data-00000-of-00001"):
        Path(f"{checkpoint}{suffix}").write_bytes(b"weights")

    code, details = ready(
        capsys, "vcf", "deepvariant", "--input", dv.sample.bam,
        "--ref", dv.sample.ref, "--checkpoint", str(checkpoint),
    )  # fmt: skip

    assert (code, details["status"]) == (0, "ready_unverified")
    assert item(details, "model")["status"] == "unverified"
    assert str(checkpoint) not in details["blocking"]
