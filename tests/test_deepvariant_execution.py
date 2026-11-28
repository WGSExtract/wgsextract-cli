"""Focused deepvariant execution regressions."""

import json
import os
import re
from pathlib import Path

import pytest

from tests.disk_volumes import GIB
from tests.htslib_helpers import (
    run_wgsextract,
    vcf_calls,
)
from tests.test_deepvariant import (
    STAGES,
    call,
    dv_flags,
    postprocess,
    rerun,
    stage_runs,
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
from wgsextract_cli.core.alignment import memory as alignment_memory
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    run_command,
)


@pytest.mark.parametrize("batch_size", ["0", "-1"])
def test_invalid_batch_size_is_refused(batch_size):
    with pytest.raises(WGSExtractError, match="must be positive"):
        run_wgsextract("vcf", "deepvariant", "-i", "s.bam", "--batch-size", batch_size)


def test_batch_size_is_not_silently_ignored_by_parabricks(dv):
    with pytest.raises(UsageError, match="--batch-size doesn't apply to Parabricks"):
        call(dv, "--batch-size", "256", "--gpu")


def test_batch_size_reaches_call_variants_without_changing_shards(dv):
    call(dv, "--batch-size", "256")
    (inference,) = dv.deepvariant.runs("call_variants")
    assert inference["opts"]["batch_size"] == "256"
    (examples,) = dv.deepvariant.runs("parallel")
    assert examples["tasks"] == ["0", "1", "2", "3"]


def test_postprocess_is_bounded_by_available_memory(
    dv, genome_fai, monkeypatch, caplog
):
    def call_with(gib, *options):
        monkeypatch.setattr(alignment_memory, "available_memory", lambda: gib * GIB)
        call(dv, *options, out=dv.tmp / f"{gib}{''.join(options)}")

    caplog.set_level("INFO")
    call_with(64)
    assert postprocess(dv) == ("4", None)  # 4 workers fit in 64 GiB.

    call_with(8)
    cpus, partitions = postprocess(dv)
    assert cpus == "4" and int(partitions) > 4
    assert "Splitting DeepVariant postprocess_variants" in caplog.text

    call_with(3)
    assert postprocess(dv)[0] == "2"
    assert "Limiting DeepVariant postprocess_variants to 2 of 4" in caplog.text

    call_with(3, "--wes")
    assert postprocess(dv) == ("4", None)  # run_deepvariant runs WES serially.

    dv.docker.clear()
    with pytest.raises(WGSExtractError, match="Not enough memory.*smaller --region"):
        call_with(1)
    assert dv.docker.runs() == []


@pytest.mark.parametrize(
    ("image", "helpfull", "partitions"),
    [
        ("google/deepvariant:1.8.0", None, False),
        ("google/deepvariant:1.9.0-gpu", None, True),
        ("local/dv@sha256:abc", "  --num_partitions: ...", True),
        ("local/dv@sha256:abc", "  --cpus: ...", False),
    ],
)
def test_num_partitions_is_used_only_when_deepvariant_has_it(
    dv, genome_fai, monkeypatch, caplog, image, helpfull, partitions
):
    monkeypatch.setitem(deepvariant.settings, "deepvariant_image", image)
    dv.docker.add_image(image)
    dv.deepvariant.set_helpfull(helpfull or "")
    # Without num_partitions, fewer workers sort bigger partitions: 2 fit here.
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 30 * GIB)
    call(dv)
    probes = [r for r in dv.docker.runs() if r["command"] == ["--helpfull"]]
    assert len(probes) == (helpfull is not None)
    # contract: the separate capability probe is isolated like the compute stages.
    assert all(p["network"] == "--network=none" for p in probes)
    if partitions:
        assert postprocess(dv)[1] is not None
        return
    assert postprocess(dv) == ("2", None)
    assert "has no --num_partitions" in caplog.text
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 8 * GIB)
    with pytest.raises(WGSExtractError, match="use DeepVariant 1.9"):
        call(dv, out=dv.tmp / "small")


def test_oom_in_call_variants_fails_fast_and_a_rerun_resumes_there(dv):
    dv.deepvariant.fail("call_variants", oom=True)
    with pytest.raises(WGSExtractError, match="ran out of memory.*lower --threads"):
        call(dv)
    assert dv.deepvariant.stages() == ["make_examples", "call_variants"]
    killed = [r["args"][1] for r in dv.docker.runs("kill")]
    assert killed == [stage_runs(dv)[-1]["name"]]
    assert visible(dv.out) == []

    rerun(dv)
    assert dv.deepvariant.stages() == ["call_variants", "postprocess_variants"]
    assert visible(dv.out) == ["deepvariant.vcf.gz", "deepvariant.vcf.gz.tbi"]
    assert not (dv.out / deepvariant.WORK_DIRECTORY).exists()

    # A finished run is kept; only resource settings changed.
    rerun(dv, "--batch-size", "256")
    assert dv.deepvariant.stages() == []


def test_a_relative_outdir_keeps_the_examples_of_a_skipped_make_examples(
    dv, monkeypatch
):
    (dv.tmp / "cwd" / "sub").mkdir(parents=True)
    monkeypatch.chdir(dv.tmp / "cwd")
    dv.deepvariant.fail("call_variants")
    with pytest.raises(WGSExtractError):
        call(dv, out="sub/../../out")
    # call_variants fails if make_examples' shards are gone.
    rerun(dv, out="sub/../../out")
    assert dv.deepvariant.stages() == ["call_variants", "postprocess_variants"]
    assert visible(dv.out) == ["deepvariant.vcf.gz", "deepvariant.vcf.gz.tbi"]
    steps = json.loads((dv.out / "deepvariant.vcf.gz.steps.json").read_text())["steps"]
    called = [Path(o["path"]).name for o in steps["call_variants"]["outputs"]]
    assert called and not [name for name in called if "make_examples" in name]


def test_a_rerun_waits_for_a_killed_runs_container(dv):
    dv.deepvariant.fail("make_examples")
    with pytest.raises(WGSExtractError):
        call(dv)
    # As if wgsextract had been SIGKILLed while its container ran on.
    stage = stage_runs(dv)[-1]
    dv.docker.keep_running(stage["name"], stage["label"])
    with pytest.raises(WGSExtractError, match=f"container {stage['name']}"):
        rerun(dv)
    assert dv.deepvariant.stages() == []

    # When no engine answers, the rerun resumes.
    dv.docker.set(ps_error="Cannot connect to the Docker daemon")
    rerun(dv)
    assert dv.deepvariant.stages() == STAGES
    assert visible(dv.out) == ["deepvariant.vcf.gz", "deepvariant.vcf.gz.tbi"]


def test_interrupting_a_stage_stops_its_container(dv):
    dv.deepvariant.fail("call_variants", interrupt=os.getpid())
    with pytest.raises(KeyboardInterrupt):
        call(dv)
    killed = [r["args"][1] for r in dv.docker.runs("kill")]
    assert killed == [stage_runs(dv)[-1]["name"]]


def test_killed_make_examples_keeps_finished_shards_only_for_the_same_run(dv):
    work = dv.out / deepvariant.WORK_DIRECTORY
    dv.deepvariant.fail("make_examples", task=2, write=False)
    with pytest.raises(WGSExtractError, match="rerun the same command to resume"):
        call(dv)
    assert len(list(work.glob("make_examples*"))) == 2

    rerun(dv)
    (examples,) = dv.deepvariant.runs("parallel")
    assert examples["tasks"] == ["2", "3"]
    assert vcf_calls(dv.out / "deepvariant.vcf.gz") == dv.sample.calls

    # Different settings start make_examples afresh, without the old shards.
    dv.deepvariant.fail("make_examples", task=2, write=False)
    with pytest.raises(WGSExtractError):
        call(dv, "--force")
    dv.deepvariant.clear()
    dv.deepvariant.fail("make_examples", task=0, write=False)
    with pytest.raises(WGSExtractError):
        call(dv, "--wes")
    (examples,) = dv.deepvariant.runs("parallel")
    assert examples["tasks"] == ["0"]
    assert not list(work.glob("make_examples*"))


def test_truncated_or_changed_stage_outputs_and_inputs_are_redone(dv):
    dv.deepvariant.fail("postprocess_variants", write=False)
    with pytest.raises(WGSExtractError):
        call(dv)
    outputs = dv.out / deepvariant.WORK_DIRECTORY / "call_variants_output.tfrecord.gz"
    outputs.write_text(outputs.read_text()[:-2])  # Truncated.
    dv.deepvariant.clear()
    with pytest.raises(WGSExtractError):
        call(dv)
    assert dv.deepvariant.stages() == ["call_variants", "postprocess_variants"]

    # The same reads, written again.
    run_command(
        ["samtools", "view", "-b", "-l", "1", "-o", f"{dv.tmp}/r.bam", dv.sample.bam]
    )
    os.replace(f"{dv.tmp}/r.bam", dv.sample.bam)
    run_command(["samtools", "index", dv.sample.bam])
    rerun(dv)
    assert dv.deepvariant.stages() == STAGES

    (dv.out / "deepvariant.vcf.gz").write_text("edited")
    rerun(dv)
    # The intermediates were removed after use, so every stage reruns.
    assert dv.deepvariant.stages() == STAGES


def test_a_resume_keeps_shards_and_reruns_for_changed_regions_or_images(dv):
    bed = dv.tmp / "targets.bed"
    bed.write_text("chr1\t0\t2000\n")
    dv.deepvariant.fail("call_variants")
    with pytest.raises(WGSExtractError):
        call(dv, "-r", str(bed))

    # Fewer --threads: the 4 finished shards are kept.
    dv.deepvariant.clear()
    with pytest.raises(WGSExtractError):
        call(dv, "-r", str(bed), threads="2")
    assert dv.deepvariant.stages() == ["call_variants"]
    assert dv_flags(dv)["num_shards"] == "4"

    bed.write_text("chr1\t0\t2500\n")
    dv.deepvariant.clear()
    with pytest.raises(WGSExtractError):
        call(dv, "-r", str(bed))
    assert dv.deepvariant.stages() == ["make_examples", "call_variants"]

    # The same tag now names another build.
    dv.docker.add_image(deepvariant.DEFAULT_IMAGE, image_id="sha256:rebuilt")
    rerun(dv, "-r", str(bed))
    assert dv.deepvariant.stages() == STAGES


def test_dry_run_leaves_an_interrupted_runs_files_alone(dv):
    dv.deepvariant.fail("call_variants")
    with pytest.raises(WGSExtractError):
        call(dv)
    work = dv.out / deepvariant.WORK_DIRECTORY
    before = sorted(p.relative_to(work) for p in work.rglob("*"))
    rerun(dv, "--dry-run")
    assert sorted(p.relative_to(work) for p in work.rglob("*")) == before
    assert dv.deepvariant.stages() == []


def test_dry_run_and_forced_steps(dv, capsys):
    dv.deepvariant.fail("postprocess_variants")
    with pytest.raises(WGSExtractError):
        call(dv)
    capsys.readouterr()

    rerun(dv, "--dry-run")
    assert dv.deepvariant.stages() == []
    plan = capsys.readouterr().out
    assert re.search(r"make_examples +skip +complete", plan)
    assert re.search(r"postprocess_variants +run +an earlier run was interrupted", plan)
    assert visible(dv.out) == []

    rerun(dv, "--force-step", "call_variants")
    assert dv.deepvariant.stages() == ["call_variants", "postprocess_variants"]

    rerun(dv, "--force")
    assert dv.deepvariant.stages() == STAGES
