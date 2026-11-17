"""Focused deepvariant gpu regressions."""

import re
import sys
from pathlib import Path

import pytest

from tests.disk_volumes import GIB
from tests.fake_tools import (
    set_gpus,
)
from tests.htslib_helpers import (
    vcf_calls,
)
from tests.test_deepvariant import (
    CPU_DIGEST,
    CPU_ID,
    OTHER_DIGEST,
    STAGES,
    as_pre601_journal,
    as_v085_journal,
    batch_warned,
    call,
    dv_flags,
    gpu_call,
    gpu_stages,
    pinned_ultima_gpu,
    recorded_images,
    recorded_params,
    rerun,
    resume_v085,
    stage_runs,
    ultima_gpu_run,
    without_gpu_driver,
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
from wgsextract_cli import api
from wgsextract_cli.commands.vcf import deepvariant
from wgsextract_cli.core.alignment import memory as alignment_memory
from wgsextract_cli.core.utils import (
    MissingDependencyError,
    UsageError,
    WGSExtractError,
)


@pytest.mark.parametrize("selection", [[], ["--model-type", "ULTIMA"]])
def test_ultima_reads_call_on_the_gpu_in_googles_ultima_gpu_image(
    ultima_gpu, selection
):
    gpu_call(ultima_gpu, *selection, "-r", "chr1:1-1500,chr1:1501-3000")

    calls = vcf_calls(ultima_gpu.out / "deepvariant.vcf.gz")
    assert calls == ultima_gpu.ultima.calls
    # contract: Parabricks has no ULTIMA model, so DeepVariant's own ULTIMA
    # model runs, in Google's GPU image, with call_variants on the GPU.
    assert dv_flags(ultima_gpu)["model_type"] == "ULTIMA"
    # contract: --gpu's comma-separated regions reach DeepVariant spaced.
    assert dv_flags(ultima_gpu)["regions"] == "chr1:1-1500 chr1:1501-3000"
    images = {r["image"] for r in ultima_gpu.docker.runs()}
    assert images == {deepvariant.DEFAULT_ULTIMA_GPU_IMAGE}
    assert gpu_stages(ultima_gpu) == [
        ("make_examples", None), ("call_variants", "all"),
        ("postprocess_variants", None),
    ]  # fmt: skip


def test_step_record_keeps_a_digest_pinned_image_reference_valid_597(ultima_gpu):
    pinned_ultima_gpu(ultima_gpu)
    gpu_call(ultima_gpu)

    # #597: v0.8.5 recorded ref@sha256:X@sha256:X, which Docker can't parse.
    images = recorded_images(ultima_gpu)
    assert set(images) == set(STAGES)
    assert set(images.values()) == {deepvariant.DEFAULT_ULTIMA_GPU_IMAGE}


@pytest.mark.parametrize("image_id", [None, OTHER_DIGEST], ids=["A==B", "A!=B"])
def test_v085_journal_with_doubled_image_digest_resumes_without_rerun_597(
    ultima_gpu, image_id
):
    resume_v085(ultima_gpu, image_id)

    assert ultima_gpu.deepvariant.stages() == []
    assert stage_runs(ultima_gpu) == []
    assert vcf_calls(ultima_gpu.out / "deepvariant.vcf.gz") == ultima_gpu.ultima.calls


def test_forced_rerun_of_v085_journal_records_the_valid_image_597(ultima_gpu):
    resume_v085(ultima_gpu, OTHER_DIGEST, "--force")

    assert ultima_gpu.deepvariant.stages() == STAGES
    images = recorded_images(ultima_gpu)
    assert set(images) == set(STAGES)
    assert set(images.values()) == {deepvariant.DEFAULT_ULTIMA_GPU_IMAGE}


def test_v085_interrupted_make_examples_keeps_its_shards_597(ultima_gpu):
    pinned_ultima_gpu(ultima_gpu)
    ultima_gpu.deepvariant.fail("make_examples", task=2, write=False)
    with pytest.raises(WGSExtractError):
        gpu_call(ultima_gpu)
    as_v085_journal(ultima_gpu, OTHER_DIGEST)
    ultima_gpu.deepvariant.fail(None)
    ultima_gpu.deepvariant.clear()
    ultima_gpu.docker.clear()

    gpu_call(ultima_gpu)

    (examples,) = ultima_gpu.deepvariant.runs("parallel")
    assert examples["tasks"][0] == "2"  # shards 0 and 1 are kept
    assert vcf_calls(ultima_gpu.out / "deepvariant.vcf.gz") == ultima_gpu.ultima.calls


def test_v085_journal_of_another_pinned_image_reruns_597(ultima_gpu, monkeypatch):
    pinned_ultima_gpu(ultima_gpu)
    gpu_call(ultima_gpu)
    as_v085_journal(ultima_gpu, OTHER_DIGEST)
    other = "google/deepvariant:gh1100-ultima-gpu@" + OTHER_DIGEST
    ultima_gpu.docker.add_image(other, image_id=OTHER_DIGEST)
    monkeypatch.setitem(deepvariant.settings, "deepvariant_ultima_gpu_image", other)
    ultima_gpu.deepvariant.clear()
    ultima_gpu.docker.clear()

    gpu_call(ultima_gpu)

    assert ultima_gpu.deepvariant.stages() == STAGES
    assert set(recorded_images(ultima_gpu).values()) == {other}


def test_step_record_names_a_pullable_image_and_its_build_601(dv):
    dv.docker.add_image(
        deepvariant.DEFAULT_IMAGE, image_id=CPU_ID, repo_digests=(CPU_DIGEST,)
    )
    call(dv)

    # #601: the record said google/deepvariant:1.10.0@<image ID>, which
    # Docker can't always pull; the image ID is now kept apart.
    records = recorded_params(dv)
    assert len(records) == len(STAGES)
    assert all(r == {"image": CPU_DIGEST, "image_id": CPU_ID} for r in records)


def test_locally_built_image_is_recorded_by_its_tag_and_build_601(dv):
    dv.docker.add_image(deepvariant.DEFAULT_IMAGE, image_id=CPU_ID)
    call(dv)

    records = recorded_params(dv)
    assert records
    assert all(
        r == {"image": deepvariant.DEFAULT_IMAGE, "image_id": CPU_ID} for r in records
    )


def test_pre601_journal_of_the_same_build_resumes_without_rerun_601(dv):
    dv.docker.add_image(
        deepvariant.DEFAULT_IMAGE, image_id=CPU_ID, repo_digests=(CPU_DIGEST,)
    )
    call(dv)
    as_pre601_journal(dv, CPU_ID)
    dv.deepvariant.clear()

    rerun(dv)

    assert dv.deepvariant.stages() == []


@pytest.mark.parametrize(
    ("old_id", "resumed"), [(CPU_ID, ["2", "3"]), ("sha256:" + "e" * 64, None)]
)
def test_pre601_interrupted_make_examples_keeps_its_shards_601(dv, old_id, resumed):
    dv.docker.add_image(
        deepvariant.DEFAULT_IMAGE, image_id=CPU_ID, repo_digests=(CPU_DIGEST,)
    )
    dv.deepvariant.fail("make_examples", task=2, write=False)
    with pytest.raises(WGSExtractError):
        call(dv)
    as_pre601_journal(dv, old_id)

    rerun(dv)

    # Only a run of the same build may keep the finished shards.
    (examples,) = dv.deepvariant.runs("parallel")
    assert examples["tasks"] == (resumed or ["0", "1", "2", "3"])
    assert vcf_calls(dv.out / "deepvariant.vcf.gz") == dv.sample.calls


def test_pre601_journal_of_a_replaced_tag_reruns_601(dv):
    dv.docker.add_image(
        deepvariant.DEFAULT_IMAGE, image_id=CPU_ID, repo_digests=(CPU_DIGEST,)
    )
    call(dv)
    as_pre601_journal(dv, "sha256:" + "e" * 64)
    dv.deepvariant.clear()

    rerun(dv)

    assert dv.deepvariant.stages() == STAGES


def test_replaced_tag_reruns_601(dv):
    dv.docker.add_image(
        deepvariant.DEFAULT_IMAGE, image_id=CPU_ID, repo_digests=(CPU_DIGEST,)
    )
    call(dv)
    new_digest = "google/deepvariant@sha256:" + "f" * 64
    dv.docker.add_image(
        deepvariant.DEFAULT_IMAGE, image_id=OTHER_DIGEST, repo_digests=(new_digest,)
    )
    dv.deepvariant.clear()

    rerun(dv)

    assert dv.deepvariant.stages() == STAGES
    assert all(
        r == {"image": new_digest, "image_id": OTHER_DIGEST}
        for r in recorded_params(dv)
    )


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        ("no driver", "No NVIDIA GPU driver"),
        ("no nvidia runtime", "NVIDIA runtime"),
        ("broken driver", "nvidia-smi failed.*couldn't communicate"),
        ("no gpu", "lists no NVIDIA GPU"),
        ("no image", "docker pull google/deepvariant:gh1100-ultima-gpu@sha256:"),
    ],
)
def test_ultima_gpu_setup_problems_are_named_before_any_work(
    ultima_gpu, monkeypatch, problem, message
):
    if problem == "no driver":
        without_gpu_driver(ultima_gpu, monkeypatch)
    elif problem == "broken driver":
        set_gpus(
            ultima_gpu.gpus, error="NVIDIA-SMI couldn't communicate with the driver"
        )
    elif problem == "no gpu":
        set_gpus(ultima_gpu.gpus)
    elif problem == "no nvidia runtime":
        ultima_gpu.docker.set(runtimes='{"runc":{}}')
    else:
        ultima_gpu.docker.set(images={})
    with pytest.raises(MissingDependencyError, match=message):
        gpu_call(ultima_gpu)
    assert ultima_gpu.docker.runs() == []


@pytest.mark.parametrize(
    ("system", "machine"),
    [("darwin", "arm64"), ("linux", "aarch64"), ("win32", "AMD64")],
)
def test_ultima_gpu_needs_x86_64_linux(ultima_gpu, monkeypatch, system, machine):
    monkeypatch.setattr(deepvariant.platform, "machine", lambda: machine)
    monkeypatch.setattr(sys, "platform", system)
    with pytest.raises(UsageError, match="--gpu is not supported"):
        gpu_call(ultima_gpu, "--model-type", "ULTIMA")
    assert ultima_gpu.docker.runs() == []


def test_a_busy_gpu_stops_before_call_variants_and_a_rerun_resumes(ultima_gpu):
    # measured (#358, RTX 3090): call_variants ran out of memory with 6,461 MiB
    # free and finished with 7,485 MiB free.
    set_gpus(ultima_gpu.gpus, (24_576, 24_576 - 6_461))
    with pytest.raises(WGSExtractError, match="free memory on each GPU"):
        gpu_call(ultima_gpu)
    assert ultima_gpu.deepvariant.stages() == ["make_examples"]

    set_gpus(ultima_gpu.gpus, (24_576, 24_576 - 7_485))
    rerun(ultima_gpu, "--gpu", reads=ultima_gpu.ultima.bam)
    assert ultima_gpu.deepvariant.stages() == ["call_variants", "postprocess_variants"]

    # A finished run needs no GPU to be kept, nor does a plan.
    set_gpus(ultima_gpu.gpus, (24_576, 23_500))
    rerun(ultima_gpu, "--gpu", reads=ultima_gpu.ultima.bam)
    rerun(ultima_gpu, "--gpu", "--dry-run", reads=ultima_gpu.ultima.bam)
    assert ultima_gpu.deepvariant.stages() == []


def test_a_busy_gpu_beside_an_idle_one_is_refused(ultima_gpu):
    # #488 review: call_variants' container gets every GPU, so the idle one
    # doesn't make room on the busy one.
    set_gpus(ultima_gpu.gpus, (24_576, 23_500), (24_576, 0))
    with pytest.raises(WGSExtractError, match="free memory on each GPU"):
        gpu_call(ultima_gpu)
    assert ultima_gpu.deepvariant.stages() == ["make_examples"]


def test_a_spaced_gpu_region_is_refused_before_reading_anything(ultima_gpu):
    # #488 review: serve --allow checks "/a.bed /b.bed" as one comma item, which
    # the depth check and DeepVariant would read as two files.
    with pytest.raises(UsageError, match="separated by commas"):
        gpu_call(ultima_gpu, "-r", "chr1:1-1500 chr1:1501-3000")
    assert ultima_gpu.docker.runs() == []
    assert not ultima_gpu.out.exists()


def test_no_gpu_memory_check_runs_on_a_busy_gpu_with_a_warning(ultima_gpu):
    set_gpus(ultima_gpu.gpus, (24_576, 23_500))
    result = api.vcf.deepvariant(
        api.VcfDeepvariantParams(
            input=Path(ultima_gpu.ultima.bam), ref=Path(ultima_gpu.sample.ref),
            outdir=ultima_gpu.out, gpu=True, no_gpu_memory_check=True,
        )
    )  # fmt: skip
    assert "deepvariant_gpu_memory" in [w.code for w in result.warnings]
    assert (ultima_gpu.out / "deepvariant.vcf.gz").is_file()


@pytest.mark.parametrize(("batch_size", "warned"), [(2048, True), (1024, False)])
def test_ultima_gpu_warns_that_the_memory_check_assumes_the_default_batch(
    ultima_gpu, batch_size, warned
):
    # #496: the 7.3 GiB check was measured at call_variants' default batch.
    assert batch_warned(ultima_gpu_run(ultima_gpu, batch_size=batch_size)) is warned
    assert ultima_gpu.deepvariant.stages() == [
        "make_examples", "call_variants", "postprocess_variants",
    ]  # fmt: skip


def test_a_finished_rerun_has_no_batch_warning(ultima_gpu):
    # #497 review: the rerun's call_variants doesn't run, nor its check.
    assert batch_warned(ultima_gpu_run(ultima_gpu, batch_size=2048))
    ultima_gpu.deepvariant.clear()
    assert not batch_warned(ultima_gpu_run(ultima_gpu, batch_size=2048))
    assert ultima_gpu.deepvariant.stages() == []


def test_a_bypassed_memory_check_has_no_batch_warning(ultima_gpu):
    # #497 review: --no-gpu-memory-check doesn't apply the check to the batch.
    result = ultima_gpu_run(ultima_gpu, batch_size=2048, no_gpu_memory_check=True)
    assert not batch_warned(result)
    assert "call_variants" in ultima_gpu.deepvariant.stages()


def test_a_gpu_plan_needs_no_gpu_driver(ultima_gpu, monkeypatch, capsys):
    without_gpu_driver(ultima_gpu, monkeypatch)
    gpu_call(ultima_gpu, "--dry-run")
    assert re.search(r"call_variants +run", capsys.readouterr().out)
    assert ultima_gpu.deepvariant.stages() == []


def test_deepvariant_states_its_host_requirement_and_refuses_too_little_ram(
    dv, monkeypatch, ram, caplog
):
    """#690: the whole-genome DeepVariant run of #431 found its RAM need by
    running into it. It now states disk, RAM and time before it starts,
    warns when its settings need more RAM than this computer has, and
    refuses one with less than it needs on one thread at a batch of 256,
    unless --no-preflight."""
    caplog.set_level("INFO")
    ram(12 * GIB)
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 12 * GIB)
    call(dv)
    assert re.search(
        r"this DeepVariant run needs \S+ GiB disk on .*, ~\d+ GiB RAM, "
        r"~[\d.]+ (h|min) on 4 threads",
        caplog.text,
    )
    assert "it may swap or be killed" in caplog.text
    assert dv.deepvariant.runs("make_examples")

    ram(4 * GIB)
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 4 * GIB)
    dv.deepvariant.clear()
    with pytest.raises(
        WGSExtractError,
        match=r"at least ~[\d.]+ GiB RAM, but this computer has 4\.0 GiB",
    ):
        call(dv, out=dv.tmp / "small")
    assert dv.deepvariant.runs("make_examples") == []
    call(dv, "--no-preflight", out=dv.tmp / "small")
    assert dv.deepvariant.runs("make_examples")


def test_deepvariant_rerun_with_finished_stages_warns_instead_of_refusing(
    dv, monkeypatch, ram, caplog
):
    """#690 review: a rerun that keeps finished stages printed no host
    requirement; it now does, and warns of a shortfall without refusing,
    since the kept stages need less than a fresh run."""
    dv.deepvariant.fail("call_variants")
    with pytest.raises(WGSExtractError):
        call(dv)
    ram(4 * GIB)
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 4 * GIB)
    caplog.clear()
    with caplog.at_level("INFO"):
        rerun(dv)
    assert "this DeepVariant run needs" in caplog.text
    assert "Running anyway (finished stages are kept)" in caplog.text
    assert dv.deepvariant.stages() == ["call_variants", "postprocess_variants"]
