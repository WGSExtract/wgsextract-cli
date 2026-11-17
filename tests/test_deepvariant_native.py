"""Focused deepvariant native regressions."""

import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.disk_volumes import GIB
from tests.fake_tools import (
    FakeParabricks,
    FakePixi,
    set_gpus,
)
from tests.htslib_helpers import (
    run_wgsextract,
    sam_to_bam,
    vcf_calls,
    write_fasta,
)
from tests.test_deepvariant import (
    ONT,
    STAGES,
    ULTIMA,
    _mac_sysctl,
    as_cram,
    call,
    native_inference,
    other_sample,
    rerun,
    visible,
)
from tests.test_deepvariant import (
    apple as apple,
)
from tests.test_deepvariant import (
    arm64 as arm64,
)
from tests.test_deepvariant import (
    big_cram as big_cram,
)
from tests.test_deepvariant import (
    dv as dv,
)
from tests.test_deepvariant import (
    genome_fai as genome_fai,
)
from tests.test_deepvariant import (
    linux_native as linux_native,
)
from tests.test_deepvariant import (
    linux_x86 as linux_x86,
)
from tests.test_deepvariant import (
    mac_native as mac_native,
)
from tests.test_deepvariant import (
    rosetta as rosetta,
)
from tests.test_deepvariant import (
    ultima_gpu as ultima_gpu,
)
from wgsextract_cli.commands.vcf import deepvariant
from wgsextract_cli.core import (
    native_deepvariant,
    parabricks,
)
from wgsextract_cli.core.alignment import memory as alignment_memory
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.utils import (
    WGSExtractError,
    run_command,
)


@pytest.mark.parametrize(
    ("configured", "message"),
    [
        (None, "Native DeepVariant image isn't installed"),
        (deepvariant.DEFAULT_IMAGE, "is linux/amd64.*AVX.*exit 134"),
    ],
    ids=["default", "configured-google-image"],
)
def test_apple_silicon_refuses_googles_amd64_image_before_any_container(
    apple, monkeypatch, configured, message
):
    """Google's amd64 image aborts under Docker Desktop's emulation (no AVX):
    refuse up front and name what does run on a Mac."""
    if configured:
        monkeypatch.setitem(deepvariant.settings, "deepvariant_image", configured)
    with pytest.raises(WGSExtractError, match=message) as error:
        call(apple)
    for alternative in ("vcf clair3", "vcf snp", "x86-64 host", "build.sh"):
        assert alternative in str(error.value)
    assert "docker pull" not in str(error.value)
    assert apple.docker.runs() == []
    assert not apple.out.exists() or visible(apple.out) == []


def test_apple_silicon_runs_the_native_arm64_image(apple):
    apple.docker.add_image(deepvariant.DEFAULT_ARM64_IMAGE, platform="linux/arm64")
    call(apple)
    assert {r["image"] for r in apple.docker.runs()} == {
        deepvariant.DEFAULT_ARM64_IMAGE
    }
    assert visible(apple.out) == ["deepvariant.vcf.gz", "deepvariant.vcf.gz.tbi"]


def test_apple_silicon_calls_with_wgse_deepvariant_without_docker_647(mac_native):
    call(mac_native)
    assert mac_native.docker.runs() == []
    assert mac_native.deepvariant.stages() == STAGES
    out = mac_native.out
    assert visible(out) == ["deepvariant.vcf.gz", "deepvariant.vcf.gz.tbi"]
    assert vcf_calls(out / "deepvariant.vcf.gz") == mac_native.sample.calls


def test_wgse_deepvariant_resumes_after_a_failed_stage_647(mac_native):
    mac_native.deepvariant.fail("call_variants")
    with pytest.raises(WGSExtractError, match="rerun the same command to resume"):
        call(mac_native)
    rerun(mac_native)
    assert mac_native.deepvariant.stages() == ["call_variants", "postprocess_variants"]
    assert vcf_calls(mac_native.out / "deepvariant.vcf.gz") == mac_native.sample.calls


def test_wgse_deepvariant_gets_a_selection_as_one_bed_of_its_intervals_647(
    mac_native,
):
    bed = mac_native.tmp / "panel.bed"
    bed.write_text("chr1\t1400\t1600\nchr1\t899\t1100\nchr1\t1050\t1200\n")
    call(mac_native, "-r", f"{bed} chr1:2001-2100")
    examples = mac_native.deepvariant.runs("wgse-deepvariant make_examples")[0]
    # contract: wgse-deepvariant calls the selection's merged intervals, in order.
    assert examples["regions_bed"] == (
        "chr1\t899\t1200\nchr1\t1400\t1600\nchr1\t2000\t2100\n"
    )
    assert (mac_native.out / "deepvariant.vcf.gz").exists()


def test_wgse_deepvariant_calls_a_selection_too_big_for_a_command_line_647(
    mac_native,
):
    """A 100,000-interval BED (exome-sized) once became one --regions
    argument longer than macOS's 1 MiB command-line limit: Argument list too
    long, reported as a stage to resume that failed again on every rerun."""
    sample, work = mac_native.sample, mac_native.tmp / "wide"
    chr1 = Path(sample.ref).read_text().split("\n")[1]
    ref = write_fasta(work / "ref.fa", {"chr1": chr1, "chrW": "ACGT" * 75_000})
    header = run_command(
        ["samtools", "view", "-H", sample.bam], capture_output=True
    ).stdout.splitlines()
    body = run_command(["samtools", "view", sample.bam], capture_output=True).stdout
    header.insert(2, "@SQ\tSN:chrW\tLN:300000")
    bam = sam_to_bam(header + body.splitlines(), work / "wide.bam")
    bed = work / "exome.bed"
    bed.write_text("".join(f"chrW\t{i}\t{i + 1}\n" for i in range(0, 300_000, 3)))
    run_wgsextract(
        "vcf", "deepvariant", "--input", bam, "--ref", str(ref),
        "--outdir", str(mac_native.out), "--threads", "2", "-r", str(bed),
    )  # fmt: skip
    assert mac_native.deepvariant.stages() == STAGES
    assert (mac_native.out / "deepvariant.vcf.gz").exists()


def test_wgse_deepvariant_reruns_for_a_changed_selection_647(mac_native):
    bed = mac_native.tmp / "panel.bed"
    bed.write_text("chr1\t899\t1100\n")
    call(mac_native, "-r", str(bed))
    bed.write_text("chr1\t899\t1600\n")
    rerun(mac_native, "-r", str(bed))
    assert mac_native.deepvariant.stages() == STAGES


def test_deepvariant_native_off_keeps_the_docker_image_647(mac_native, monkeypatch):
    monkeypatch.setitem(deepvariant.settings, "deepvariant_native", "off")
    mac_native.docker.add_image(deepvariant.DEFAULT_ARM64_IMAGE, platform="linux/arm64")
    call(mac_native)
    assert mac_native.deepvariant.runs("wgse-deepvariant make_examples") == []
    assert {r["image"] for r in mac_native.docker.runs()} == {
        deepvariant.DEFAULT_ARM64_IMAGE
    }


def test_wgse_deepvariant_reads_cram_31_without_staging_647(mac_native):
    cram = as_cram(mac_native.sample, "3.1")
    call(mac_native, reads=cram)
    reads = mac_native.deepvariant.runs("wgse-deepvariant make_examples")[0]["opts"]
    assert reads["reads"] == cram
    assert vcf_calls(mac_native.out / "deepvariant.vcf.gz") == mac_native.sample.calls


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("unset", "deepvariant_weights isn't set"),
        ("missing", "wgs.dvw is missing"),
        ("changed", "isn't the published one"),
    ],
)
def test_wgse_deepvariant_needs_its_published_weights_647(
    mac_native, monkeypatch, change, message
):
    if change == "unset":
        monkeypatch.setitem(deepvariant.settings, "deepvariant_weights", None)
    elif change == "missing":
        (mac_native.weights / "wgs.dvw").unlink()
    else:
        (mac_native.weights / "wgs_small_weights" / "layer_2_bias.npy").write_text("x")
    with pytest.raises(WGSExtractError, match=message) as error:
        call(mac_native)
    assert "native/deepvariant/README.md" in str(error.value)
    assert mac_native.deepvariant.stages() == []


@pytest.mark.parametrize(
    "selection", [("--wes",), ("--pacbio",), ("--checkpoint", "x")]
)
def test_wgse_deepvariant_refuses_unqualified_models_647(mac_native, selection):
    with pytest.raises(WGSExtractError, match="only the WGS model"):
        call(mac_native, *selection)
    assert mac_native.deepvariant.stages() == []
    assert mac_native.docker.runs() == []


def test_wgse_deepvariant_fits_a_16_gb_mac_647(mac_native, monkeypatch):
    """The Linux ARM64 image's TensorFlow call_variants needs ~27 GiB; the
    Mac build's GPU batch follows the Mac's memory and fits 16 GB."""
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 10 * GIB)
    total = SimpleNamespace(total=16 * GIB, available=10 * GIB)
    monkeypatch.setattr(native_deepvariant.psutil, "virtual_memory", lambda: total)
    call(mac_native)
    planned = mac_native.deepvariant.runs("wgse-deepvariant call_variants")[0]
    assert planned["opts"]["batch_size"] == "512"
    assert (mac_native.out / "deepvariant.vcf.gz").exists()


def test_wgse_deepvariant_0_1_0_is_refused_for_its_pileup_drift_647(mac_native):
    """0.1.0 didn't trim reads to the pileup window, so 20 HG003 records
    differed from Google's VCF; 0.1.1 is the first build that matches."""
    state = json.loads((mac_native.deepvariant.root / "state.json").read_text())
    state["wgse_version"] = "0.1.0"
    (mac_native.deepvariant.root / "state.json").write_text(json.dumps(state))
    with pytest.raises(WGSExtractError, match=r"0\.1\.0 is too old.*0\.1\.1 or later"):
        call(mac_native)
    assert not (mac_native.out / "deepvariant.vcf.gz").exists()


def test_linux_calls_with_wgse_deepvariant_on_the_cpu_without_docker_719(
    linux_native,
):
    call(linux_native)
    assert linux_native.docker.runs() == []
    assert linux_native.deepvariant.stages() == STAGES
    # contract: without --gpu, call_variants never takes the GPU.
    assert native_inference(linux_native)["inference_backend"] == "cpu"
    out = linux_native.out
    assert visible(out) == ["deepvariant.vcf.gz", "deepvariant.vcf.gz.tbi"]
    assert vcf_calls(out / "deepvariant.vcf.gz") == linux_native.sample.calls


def test_gpu_calls_with_wgse_deepvariant_on_cuda_without_parabricks_719(
    linux_native,
):
    call(linux_native, "--gpu", "-r", "chr1:1-1500,chr1:1501-3000")
    assert linux_native.docker.runs() == []
    # contract: --gpu runs call_variants on CUDA, never silently on the CPU.
    assert native_inference(linux_native)["inference_backend"] == "cuda"
    calls = vcf_calls(linux_native.out / "deepvariant.vcf.gz")
    assert calls == linux_native.sample.calls


def test_gpu_finds_wgse_deepvariant_in_its_pixi_environment_719(
    linux_native, monkeypatch
):
    """`pixi install -e deepvariant` puts wgse-deepvariant in the workspace's
    deepvariant environment, not on PATH; --gpu runs it from there."""
    pixi = FakePixi(linux_native.tmp / "pixi", None)
    bindir = pixi.env("deepvariant") / "bin"
    for tool in linux_native.deepvariant.native_bin.iterdir():
        shutil.move(tool, bindir / tool.name)
    monkeypatch.setenv("PIXI_EXE", str(pixi.exe))
    monkeypatch.delenv("PIXI_ENVIRONMENT_NAME", raising=False)
    clear_tool_caches()
    call(linux_native, "--gpu", "-r", "chr1:1-1500,chr1:1501-3000")
    assert linux_native.docker.runs() == []
    assert native_inference(linux_native)["inference_backend"] == "cuda"
    calls = vcf_calls(linux_native.out / "deepvariant.vcf.gz")
    assert calls == linux_native.sample.calls


def test_an_unfinished_pixi_install_keeps_googles_image_719(linux_native, monkeypatch):
    """#748 review: an interrupted `pixi install -e deepvariant` can leave the
    binary without its libraries; only a finished install is used."""
    pixi = FakePixi(linux_native.tmp / "pixi", None)
    bindir = pixi.env("deepvariant", installed=False) / "bin"
    for tool in linux_native.deepvariant.native_bin.iterdir():
        shutil.move(tool, bindir / tool.name)
    monkeypatch.setenv("PIXI_EXE", str(pixi.exe))
    monkeypatch.delenv("PIXI_ENVIRONMENT_NAME", raising=False)
    clear_tool_caches()
    call(linux_native)
    assert linux_native.deepvariant.runs("wgse-deepvariant call_variants") == []
    assert linux_native.docker.runs() != []


def test_gpu_with_deepvariant_native_off_runs_parabricks_719(
    linux_native, monkeypatch, ram
):
    ram(128 * 10**9)  # above Parabricks' RAM floor
    monkeypatch.setitem(deepvariant.settings, "deepvariant_native", "off")
    host = FakeParabricks(linux_native.tmp / "gpu", parabricks.DEFAULT_IMAGE)
    monkeypatch.setenv("PATH", f"{host.bin}{os.pathsep}{os.environ['PATH']}")
    clear_tool_caches()
    call(linux_native, "--gpu")
    assert [tool for tool, _ in host.runs()] == ["deepvariant"]
    assert linux_native.deepvariant.runs("wgse-deepvariant run") == []


def test_a_busy_gpu_stops_wgse_deepvariant_before_call_variants_719(linux_native):
    set_gpus(linux_native.gpus, (24_576, 24_576 - 2_048))
    with pytest.raises(WGSExtractError, match="free memory on GPU 0"):
        call(linux_native, "--gpu")
    assert linux_native.deepvariant.stages() == ["make_examples"]

    set_gpus(linux_native.gpus, (24_576, 0))
    rerun(linux_native, "--gpu")
    assert linux_native.deepvariant.stages() == [
        "call_variants",
        "postprocess_variants",
    ]


def test_ont_reads_keep_googles_image_and_parabricks_before_0_3_0_739(
    linux_native, monkeypatch, ram
):
    """#739: wgse-deepvariant 0.2.0's ONT make_examples didn't phase reads as
    Google's does, so it leaves ONT reads to Google's image and Parabricks."""
    ram(128 * 10**9)  # above Parabricks' RAM floor
    reads = other_sample(linux_native, ONT)
    call(linux_native, reads=reads.bam)
    assert linux_native.deepvariant.runs("wgse-deepvariant run") == []
    assert {r["image"] for r in linux_native.docker.runs()} == {
        deepvariant.DEFAULT_IMAGE
    }

    host = FakeParabricks(linux_native.tmp / "gpu", parabricks.DEFAULT_IMAGE)
    monkeypatch.setenv("PATH", f"{host.bin}{os.pathsep}{os.environ['PATH']}")
    clear_tool_caches()
    call(linux_native, "--gpu", reads=reads.bam, out=linux_native.tmp / "gpu-out")
    (run,) = host.runs()
    # contract: Parabricks calls ONT reads with its ONT mode.
    assert run[0] == "deepvariant" and "ont" in run[1]
    assert linux_native.deepvariant.runs("wgse-deepvariant run") == []


@pytest.mark.parametrize("gpu", [False, True])
def test_ont_reads_call_with_wgse_deepvariant_0_3_0_739(linux_native, gpu):
    """#739: from 0.3.0 wgse-deepvariant calls ONT reads as Google's image
    does, on the CPU and, with --gpu, on CUDA instead of Parabricks."""
    linux_native.deepvariant.set_native_version("0.3.0")
    reads = other_sample(linux_native, ONT)
    call(linux_native, *(["--gpu"] if gpu else []), reads=reads.bam)
    assert linux_native.docker.runs() == []
    (run,) = linux_native.deepvariant.runs("wgse-deepvariant run")
    # contract: the ONT_R104 model, with its own checked weights.
    assert run["opts"]["model_type"] == "ONT_R104"
    assert run["opts"]["checkpoint"] == str(linux_native.weights / "ont_r104.dvw")
    assert native_inference(linux_native)["inference_backend"] == (
        "cuda" if gpu else "cpu"
    )
    assert visible(linux_native.out) == ["deepvariant.vcf.gz", "deepvariant.vcf.gz.tbi"]


def test_ont_reads_need_the_ont_r104_weights_739(linux_native):
    """#739: a weights folder from before 0.3.0 has only the WGS model's."""
    linux_native.deepvariant.set_native_version("0.3.0")
    shutil.rmtree(linux_native.weights / "ont_r104_small_weights")
    reads = other_sample(linux_native, ONT)
    with pytest.raises(WGSExtractError, match="ont_r104_small_weights.* is missing"):
        call(linux_native, reads=reads.bam)
    assert linux_native.deepvariant.stages() == []


def test_linux_leaves_other_models_to_googles_image_719(linux_native):
    call(linux_native, "--wes")
    assert linux_native.deepvariant.runs("wgse-deepvariant run") == []
    assert {r["image"] for r in linux_native.docker.runs()} == {
        deepvariant.DEFAULT_IMAGE
    }


@pytest.mark.parametrize(
    ("problem", "message"),
    [("cpu build", "CPU-only build"), ("no gpu", "no NVIDIA GPU")],
)
def test_gpu_without_cuda_stops_before_make_examples_719(
    linux_native, problem, message
):
    """#733 review: a CPU-only package or a missing driver was found only at
    call_variants, after make_examples had run."""
    if problem == "cpu build":
        linux_native.deepvariant.set_native_version("0.2.0", "CPU only")
    else:
        set_gpus(linux_native.gpus, error="couldn't communicate with the NVIDIA driver")
    with pytest.raises(WGSExtractError, match=message):
        call(linux_native, "--gpu")
    assert linux_native.deepvariant.stages() == []
    # A plan needs no GPU.
    call(linux_native, "--gpu", "--dry-run")


def test_resumed_cpu_inference_uses_this_runs_threads_719(linux_native):
    """#733 review: a resumed run kept its first run's shard count, and
    CPU inference took that many threads instead of this run's."""
    linux_native.deepvariant.fail("call_variants")
    with pytest.raises(WGSExtractError, match="resume"):
        call(linux_native, threads="4")
    rerun(linux_native, threads="2")
    assert native_inference(linux_native)["cpu_threads"] == "2"


def test_an_old_build_doesnt_stop_ont_reads_using_googles_image_719(linux_native):
    """#733 review: the build was checked as if for WGS before the reads
    chose ONT_R104, which it doesn't call."""
    linux_native.deepvariant.set_native_version("0.1.0")
    reads = other_sample(linux_native, ONT)
    call(linux_native, reads=reads.bam)
    assert {r["image"] for r in linux_native.docker.runs()} == {
        deepvariant.DEFAULT_IMAGE
    }


def test_linux_without_weights_set_keeps_googles_image_719(linux_native, monkeypatch):
    """#733 review: an installed build without deepvariant_weights stopped
    runs that Google's image (and Parabricks) used to make."""
    monkeypatch.setitem(deepvariant.settings, "deepvariant_weights", None)
    call(linux_native)
    assert linux_native.deepvariant.runs("wgse-deepvariant run") == []
    assert {r["image"] for r in linux_native.docker.runs()} == {
        deepvariant.DEFAULT_IMAGE
    }


def test_linux_refuses_the_apple_silicon_only_wgse_deepvariant_719(linux_native):
    linux_native.deepvariant.set_native_version("0.1.0")
    with pytest.raises(WGSExtractError, match="0.1.0 is too old"):
        call(linux_native)
    assert linux_native.deepvariant.stages() == []


def test_rosetta_python_still_refuses_googles_amd64_image(rosetta, monkeypatch):
    monkeypatch.setitem(
        deepvariant.settings, "deepvariant_image", deepvariant.DEFAULT_IMAGE
    )
    with pytest.raises(WGSExtractError, match="is linux/amd64.*AVX"):
        call(rosetta)
    assert rosetta.docker.runs() == []


def test_rosetta_python_runs_the_native_arm64_image(rosetta):
    rosetta.docker.add_image(deepvariant.DEFAULT_ARM64_IMAGE, platform="linux/arm64")
    call(rosetta)
    assert {r["image"] for r in rosetta.docker.runs()} == {
        deepvariant.DEFAULT_ARM64_IMAGE
    }


def test_an_amd64_image_on_an_arm64_engine_is_refused_whatever_the_host_says(dv):
    """The engine/image pair is checked on the x86-64 path too (#665)."""
    dv.docker.set(arch="aarch64")
    with pytest.raises(WGSExtractError, match="is linux/amd64.*AVX"):
        call(dv)
    assert dv.docker.runs() == []


def test_intel_mac_runs_googles_image(dv, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    _mac_sysctl(dv, "0")
    call(dv)
    assert {r["image"] for r in dv.docker.runs()} == {deepvariant.DEFAULT_IMAGE}


@pytest.mark.parametrize(
    ("engine", "image", "message"),
    [
        ("x86_64", "linux/arm64", "native ARM64 Docker engine"),
        ("aarch64", "linux/amd64", "is linux/amd64.*AVX"),
        ("aarch64", "", "must be linux/arm64"),
    ],
)
def test_arm_refuses_emulation_before_reading_input(arm64, engine, image, message):
    arm64.docker.set(arch=engine)
    arm64.docker.add_image(deepvariant.DEFAULT_ARM64_IMAGE, platform=image)
    with pytest.raises(WGSExtractError, match=message):
        call(arm64, reads=arm64.tmp / "missing.bam")
    assert arm64.docker.runs() == []


def test_arm_runs_the_native_image_past_its_entrypoint_and_local_launchers(
    arm64, monkeypatch
):
    """Community ARM images' entrypoints auto-select INT8/BF16 models, and a
    run_deepvariant on PATH isn't audited: each stage runs its own binary in
    the local image, staged reads included."""
    monkeypatch.setenv(
        "PATH", f"{arm64.deepvariant.bin}{os.pathsep}{os.environ['PATH']}"
    )
    monkeypatch.setitem(
        deepvariant.settings, "deepvariant_image", "local/native@sha256:abc"
    )
    arm64.docker.add_image("local/native@sha256:abc", platform="linux/arm64")
    clear_tool_caches()
    call(arm64, "-r", "chr1:600-700 chr1:2000-2100", reads=as_cram(arm64.sample, "3.1"))

    plan, *stages = arm64.docker.runs()
    assert {r["image"] for r in [plan, *stages]} == {"local/native@sha256:abc"}
    # contract: never pull, and bypass the image's entrypoint.
    assert {r["pull"] for r in [plan, *stages]} == {"--pull=never"}
    assert plan["entrypoint"] == "/opt/deepvariant/bin/run_deepvariant"
    assert [s["entrypoint"] for s in stages] == ["/bin/bash"] * 3
    (examples,) = arm64.deepvariant.runs("make_examples")
    assert Path(examples["reads"]).name == "reads.cram"
    assert examples["tmpdir"] == str(arm64.out / deepvariant.WORK_DIRECTORY / ".tmp")
    assert visible(arm64.out) == ["deepvariant.vcf.gz", "deepvariant.vcf.gz.tbi"]


@pytest.mark.parametrize(
    "selection",
    [
        *(["--model-type", m] for m in
          ("ULTIMA", "WES", "PACBIO", "ONT_R104", "HYBRID_PACBIO_ILLUMINA")),
        ["--pacbio"],
        ["--wes"],
        ["--ont"],
        ["--checkpoint", "custom/model.ckpt"],
    ],
)  # fmt: skip
def test_arm_unqualified_models_fail_before_docker_or_input_work(arm64, selection):
    with pytest.raises(WGSExtractError, match="not qualified.*only the default WGS"):
        call(arm64, *selection, reads=arm64.tmp / "missing.bam")
    assert arm64.docker.runs(None) == []


@pytest.mark.parametrize(("model", "reads"), [("ONT_R104", (ONT,)), ("ULTIMA", ULTIMA)])
def test_arm_detected_non_wgs_reads_are_refused(arm64, model, reads):
    sample = other_sample(arm64, *reads[:1], tags="".join(reads[1:]))
    with pytest.raises(WGSExtractError, match=f"{model}.*not qualified"):
        call(arm64, reads=sample.bam)
    assert arm64.docker.runs() == []


def test_arm_missing_native_image_gives_build_instructions(arm64):
    arm64.docker.set(images={})
    with pytest.raises(
        WGSExtractError, match="packaging/deepvariant-arm64/build.sh"
    ) as error:
        call(arm64)
    assert "docker pull" not in str(error.value)


def test_arm_run_reports_the_deepvariant_preflight(arm64, caplog):
    with caplog.at_level("INFO"):
        call(arm64)
    assert "Preflight deepvariant" in caplog.text
    assert "DeepVariant WGS uses sequential stages" in caplog.text
    assert "postprocess_variants: 4 workers" in caplog.text
