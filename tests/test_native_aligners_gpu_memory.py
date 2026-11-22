"""Focused native aligners gpu memory regressions."""

import json
import shutil
from argparse import Namespace

import psutil
import pytest

from tests.fake_tools import python_tool
from tests.htslib_helpers import needs_tools, write_fasta
from tests.test_alignment_commands import _samtools, _short_reads
from tests.test_native_aligners import (
    _GENOME_SIZED,
    GIB,
    _align,
    _cuda_capabilities,
    _install_build,
    _mapper,
    _native,
    _needed,
    _run_realign,
    _sort_memory,
)
from tests.test_native_aligners import (
    apple_silicon as apple_silicon,
)
from tests.test_native_aligners import (
    bwa_bin as bwa_bin,
)
from tests.test_native_aligners import (
    glibc_2_44 as glibc_2_44,
)
from tests.test_native_aligners import (
    linux as linux,
)
from tests.test_native_aligners import (
    linux_x86_64 as linux_x86_64,
)
from tests.test_native_aligners import (
    mem3_bin as mem3_bin,
)
from tests.test_realign import _old_alignment
from wgsextract_cli.commands.alignment import common, preparation, short_reads
from wgsextract_cli.core import native_aligners
from wgsextract_cli.core.alignment.memory import (
    bwa_gpu_memory,
    estimate_mapping_memory,
)
from wgsextract_cli.core.config import settings


def _windows_bwa_candidate(tmp_path, capabilities):
    real = shutil.which("bwa")
    assert real is not None
    return python_tool(
        tmp_path / "custom aligner",
        "custom-bwa",
        f"""\
import subprocess, sys
args = sys.argv[1:]
if args[:1] == ["capabilities"]:
    sys.stdout.write({capabilities!r})
    sys.exit(0)
if args[:1] == ["--backend"]:
    args = args[2:]
sys.exit(subprocess.call([{real!r}, *args]))
""",
    )


@needs_tools("bwa", "samtools")
@pytest.mark.parametrize(
    ("mode", "backend", "reason"),
    [
        ("missing", None, None),
        ("unusable", None, None),
        ("cpu", "cpu", None),
        ("cuda-defaults-to-cpu", "cpu", None),
        ("cuda", "cuda", None),
        ("cuda-config", "cuda", None),
        ("cuda-config-disabled", "cpu", None),
        ("low-ram", "cpu", "memory_budget_cpu"),
        ("low-commit", "cpu", "windows_commit_budget_cpu"),
        ("commit-stepdown", "cuda", "windows_commit_budget_low_sort"),
        ("ram-and-commit-same-step", "cuda", "memory_budget_cuda_low_sort"),
        ("commit-steps-past-ram", "cuda", "windows_commit_budget_low_sort"),
    ],
)
def test_issue1119_windows_optional_native_bwa_and_commit_fallback(
    tmp_path, monkeypatch, ram, caplog, mode, backend, reason
):
    """Only a valid native build runs, and Windows CUDA is opt-in and budgeted."""
    monkeypatch.setattr(
        native_aligners,
        "platform",
        Namespace(system=lambda: "Windows", machine=lambda: "AMD64"),
    )
    ram(11 * GIB // 2 if mode == "low-ram" else 64 * GIB)
    caplog.set_level("INFO")
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    ram_budget = (_needed(reads, "1G") + _needed(reads, "768M")) // 2
    if mode in {"ram-and-commit-same-step", "commit-steps-past-ram"}:
        ram(2 * GIB + ram_budget)
    candidate = tmp_path / "missing" / "wgse-bwa.exe"
    if mode != "missing":
        capabilities = (
            "not JSON"
            if mode == "unusable"
            else _cuda_capabilities("bwa", cuda_available=mode != "cpu")
        )
        candidate = _windows_bwa_candidate(tmp_path, capabilities)
    monkeypatch.setitem(settings, "wgse_bwa_path", str(candidate))
    commit = {
        "low-commit": 4 * GIB,
        "commit-stepdown": 23 * GIB // 4,
        "ram-and-commit-same-step": 23 * GIB // 4,
        "commit-steps-past-ram": 21 * GIB // 4,
    }
    monkeypatch.setattr(
        short_reads, "windows_available_commit", lambda: commit.get(mode, 32 * GIB)
    )
    # Exercise the real samtools pipeline in a deployment without optional samblaster.
    resolve = common.get_tool_path

    def without_samblaster(tool):
        return None if tool == "samblaster" else resolve(tool)

    monkeypatch.setattr(common, "get_tool_path", without_samblaster)
    monkeypatch.setattr(preparation, "get_tool_path", without_samblaster)
    monkeypatch.setitem(settings, "prefer_native_gpu", mode.startswith("cuda-config"))
    options = (
        ["--prefer-native-gpu"]
        if mode in {"cuda", "low-ram", *commit}
        else ["--no-prefer-native-gpu"]
        if mode == "cuda-config-disabled"
        else []
    )
    sort_memory = "1G" if mode in commit and mode != "low-commit" else "256M"
    bam, result = _align(reads, tmp_path / "out", *options, memory=sort_memory)
    upstream, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")

    assert _samtools("view", bam) == _samtools("view", upstream)
    assert result.metrics.get("native_aligner_backend") == backend
    assert result.metrics.get("native_aligner_backend_reason") == reason
    assert ("native_aligner_unusable" in {w.code for w in result.warnings}) == (
        mode in {"missing", "unusable"}
    )
    if mode == "low-commit":
        assert "Windows commit" in caplog.text
    if mode in {"commit-stepdown", "ram-and-commit-same-step"}:
        assert result.metrics["native_aligner_sort_memory_actual"] == "768M"
    if mode == "ram-and-commit-same-step":
        assert result.metrics["native_aligner_memory_budget_bytes"] == ram_budget
    if mode == "commit-steps-past-ram":
        assert result.metrics["native_aligner_sort_memory_actual"] == "512M"
        assert result.metrics["native_aligner_memory_budget_bytes"] == commit[mode]


@needs_tools("bwa", "samtools")
def test_issue1119_realign_reruns_when_windows_cuda_is_enabled(
    tmp_path, monkeypatch, ram
):
    monkeypatch.setattr(
        native_aligners,
        "platform",
        Namespace(system=lambda: "Windows", machine=lambda: "AMD64"),
    )
    monkeypatch.setattr(short_reads, "windows_available_commit", lambda: 32 * GIB)
    candidate = _windows_bwa_candidate(tmp_path, _cuda_capabilities("bwa"))
    monkeypatch.setitem(settings, "wgse_bwa_path", str(candidate))
    monkeypatch.setitem(settings, "prefer_native_gpu", False)
    ram(64 * GIB)
    reads = _short_reads(tmp_path)
    source, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")
    out = tmp_path / "out"
    options = {"r1": reads.r1, "r2": reads.r2, "threads": 2, "memory": "256M"}

    first = _run_realign(source, reads.ref, out, **options)
    (bam,) = out.glob("*_aligned.bam")
    assert first.metrics["native_aligner_backend"] == "cpu"
    control = _samtools("view", str(bam))

    opted_in = _run_realign(source, reads.ref, out, prefer_native_gpu=True, **options)
    assert opted_in.metrics["native_aligner_backend"] == "cuda"
    assert _samtools("view", str(bam)) == control


@pytest.mark.usefixtures("apple_silicon")
def test_a_mac_short_of_ram_for_metal_sorts_in_less_memory(tmp_path, bwa_bin, ram):
    # #480: a 16 GB Mac. Sorting with less memory leaves the output unchanged;
    # the batch size (-K) never changes.
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    _install_build(bwa_bin, "bwa")
    upstream_bam, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")
    ram(2 * GIB + (_needed(reads, "1G") + _needed(reads, "768M")) // 2)

    bam, result = _align(reads, tmp_path / "out")

    assert _mapper(bam).startswith("wgse-bwa --backend auto mem -K 100000000 ")
    assert _sort_memory(bam) == "768M"
    assert _samtools("view", bam) == _samtools("view", upstream_bam)
    assert result.metrics["native_aligner_backend"] == "metal"


@pytest.mark.usefixtures("apple_silicon")
def test_a_mac_too_small_for_metal_runs_wgse_bwa_on_the_cpu(tmp_path, bwa_bin, ram):
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    _install_build(bwa_bin, "bwa")
    ram(2 * GIB + _needed(reads, "256M") - GIB // 4)

    bam, result = _align(reads, tmp_path / "out", memory="256M")

    assert _mapper(bam).startswith("wgse-bwa mem -K 100000000 ")
    assert _sort_memory(bam) == "256M"
    assert result.metrics["native_aligner_backend"] == "cpu"


@pytest.mark.usefixtures("apple_silicon")
def test_issue747_gpu_does_not_inherit_an_over_budget_cpu_floor(
    tmp_path, bwa_bin, ram, caplog
):
    caplog.set_level("INFO")
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    _install_build(bwa_bin, "bwa")
    upstream_bam, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")
    budget = _needed(reads, "256M") - GIB // 4
    ram(2 * GIB + budget)

    bam, result = _align(reads, tmp_path / "out")

    assert _sort_memory(bam) == "256M"
    assert _samtools("view", bam) == _samtools("view", upstream_bam)
    assert result.metrics["native_aligner_backend"] == "cpu"
    assert result.metrics["native_aligner_backend_reason"] == "memory_budget_cpu"
    assert result.metrics["native_aligner_memory_estimate_bytes"] > budget
    assert result.metrics["native_aligner_memory_budget_bytes"] == budget
    assert result.metrics["native_aligner_sort_memory_requested"] == "1G"
    assert result.metrics["native_aligner_sort_memory_actual"] == "256M"
    assert "2.5x slower" in caplog.text
    assert "--prefer-native-gpu" in caplog.text


@pytest.mark.parametrize("host", ["apple_silicon", "linux_x86_64"])
def test_issue747_large_ram_host_keeps_gpu_when_momentary_available_is_low(
    tmp_path, bwa_bin, ram, request, host
):
    request.getfixturevalue(host)
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    capabilities = _cuda_capabilities("bwa") if host == "linux_x86_64" else None
    _install_build(bwa_bin, "bwa", capabilities)
    ram(64 * GIB, available=GIB)

    bam, result = _align(reads, tmp_path / "out")

    assert result.metrics["native_aligner_backend"] == (
        "cuda" if host == "linux_x86_64" else "metal"
    )
    assert _sort_memory(bam) == "1G"


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("preference", ["flag", "config", "config_disabled_by_flag"])
def test_issue747_speed_opt_in_preserves_gpu_and_explicit_sort_memory(
    tmp_path, bwa_bin, ram, monkeypatch, preference
):
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    _install_build(bwa_bin, "bwa")
    control, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")
    cpu = _needed(reads, "384M") - bwa_gpu_memory(reads.ref, 2 * 140)
    ram(2 * GIB + cpu - GIB // 4)
    options = ["--prefer-native-gpu"] if preference == "flag" else []
    if preference != "flag":
        monkeypatch.setitem(settings, "prefer_native_gpu", True)
    if preference == "config_disabled_by_flag":
        options = ["--no-prefer-native-gpu"]

    bam, result = _align(reads, tmp_path / "out", *options, memory="384M")

    gpu = preference != "config_disabled_by_flag"
    assert result.metrics["native_aligner_backend"] == ("metal" if gpu else "cpu")
    assert _sort_memory(bam) == ("384M" if gpu else "256M")
    assert result.metrics["native_aligner_backend_reason"] == (
        "prefer_gpu" if gpu else "memory_budget_cpu"
    )
    assert _samtools("view", bam) == _samtools("view", control)


@pytest.mark.usefixtures("apple_silicon")
def test_issue747_resume_preserves_fallback_explanation_and_speed_opt_in_reruns(
    tmp_path, bwa_bin, ram
):
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    _install_build(bwa_bin, "bwa")
    source, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")
    ram(2 * GIB + _needed(reads, "256M") - GIB // 4)
    out = tmp_path / "out"

    def run(**options):
        return _run_realign(
            source, reads.ref, out, r1=reads.r1, r2=reads.r2,
            memory="1G", threads=2, no_preflight=True, **options,
        )  # fmt: skip

    first = run()
    (bam,) = out.glob("*_aligned.bam")
    written = bam.stat().st_mtime_ns
    resumed = run()
    assert bam.stat().st_mtime_ns == written
    assert _native(resumed) == _native(first)
    assert resumed.metrics["native_aligner_backend_reason"] == "memory_budget_cpu"
    assert (
        resumed.metrics["native_aligner_memory_estimate_bytes"]
        > (resumed.metrics["native_aligner_memory_budget_bytes"])
    )
    assert resumed.metrics["native_aligner_sort_memory_actual"] == _sort_memory(
        str(bam)
    )
    control = _samtools("view", str(bam))

    preferred = run(prefer_native_gpu=True)
    assert bam.stat().st_mtime_ns != written
    assert preferred.metrics["native_aligner_backend"] == "metal"
    assert preferred.metrics["native_aligner_sort_memory_actual"] == _sort_memory(
        str(bam)
    )
    assert _samtools("view", str(bam)) == control


@pytest.mark.usefixtures("apple_silicon")
def test_issue747_preference_tries_gpu_sort_stepdown_before_over_budget_override(
    tmp_path, bwa_bin, ram, monkeypatch
):
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    _install_build(bwa_bin, "bwa")
    control, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")
    ram(2 * GIB + (_needed(reads, "1G") + _needed(reads, "768M")) // 2)
    monkeypatch.setitem(settings, "prefer_native_gpu", True)

    bam, result = _align(reads, tmp_path / "out")

    assert result.metrics["native_aligner_backend"] == "metal"
    assert _sort_memory(bam) == "768M"
    assert (
        result.metrics["native_aligner_backend_reason"]
        == "memory_budget_metal_low_sort"
    )
    assert _samtools("view", bam) == _samtools("view", control)


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("memory_source", ["default", "config", "explicit"])
def test_issue747_over_budget_preference_preserves_only_explicit_sort_memory(
    tmp_path, bwa_bin, ram, monkeypatch, memory_source
):
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    _install_build(bwa_bin, "bwa")
    cpu = _needed(reads, "384M") - bwa_gpu_memory(reads.ref, 2 * 140)
    ram(2 * GIB + cpu - GIB // 4)
    monkeypatch.setitem(settings, "prefer_native_gpu", True)
    if memory_source == "config":
        monkeypatch.setitem(settings, "memory_limit", "1G")

    bam, result = _align(
        reads,
        tmp_path / "out",
        memory="384M" if memory_source == "explicit" else None,
    )

    assert result.metrics["native_aligner_backend"] == "metal"
    assert result.metrics["native_aligner_backend_reason"] == "prefer_gpu"
    assert _sort_memory(bam) == ("384M" if memory_source == "explicit" else "256M")


@pytest.mark.usefixtures("apple_silicon")
def test_issue747_preference_does_not_rerun_an_already_fitting_gpu_alignment(
    tmp_path, bwa_bin, ram
):
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    _install_build(bwa_bin, "bwa")
    source, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")
    ram(2 * GIB + (_needed(reads, "1G") + _needed(reads, "768M")) // 2)
    out = tmp_path / "out"
    options = {
        "r1": reads.r1,
        "r2": reads.r2,
        "memory": "1G",
        "threads": 2,
        "no_preflight": True,
    }
    first = _run_realign(source, reads.ref, out, **options)
    (bam,) = out.glob("*_aligned.bam")
    written = bam.stat().st_mtime_ns

    resumed = _run_realign(source, reads.ref, out, prefer_native_gpu=True, **options)

    assert bam.stat().st_mtime_ns == written
    assert _native(first) == _native(resumed)
    journal = json.loads(bam.with_suffix(".bam.steps.json").read_text())
    assert "prefer_native_gpu" not in journal["steps"]["align"]["params"]


@needs_tools("bwa", "samtools", "samblaster")
def test_issue747_real_metal_realign_adapts_to_low_physical_memory(
    tmp_path, monkeypatch
):
    native = native_aligners.find_native_aligner("bwa", "auto")
    if native is None or native.gpu != "metal":
        pytest.skip("Requires the installed real wgse-bwa Metal build")
    reads = _short_reads(tmp_path)
    (tmp_path / "old").mkdir()
    source, pairs = _old_alignment(tmp_path, reads.genome)
    memory = psutil.virtual_memory()
    monkeypatch.setattr(
        psutil,
        "virtual_memory",
        lambda: memory._replace(total=16 * GIB, available=14 * GIB),
    )
    baseline_out = tmp_path / "baseline"
    baseline = _run_realign(
        source, reads.ref, baseline_out, memory="1G", aligner="bwa", no_preflight=False
    )
    monkeypatch.setattr(
        psutil,
        "virtual_memory",
        lambda: memory._replace(total=4 * GIB, available=2 * GIB),
    )
    low_out = tmp_path / "low-memory"
    low = _run_realign(
        source, reads.ref, low_out, memory="1G", aligner="bwa", no_preflight=False
    )

    baseline_bam = str(baseline_out / "x_aligned.bam")
    low_bam = str(low_out / "x_aligned.bam")
    assert baseline.metrics["native_aligner_backend"] == "metal"
    assert low.metrics["native_aligner_backend"] == "cpu"
    # Input sizing already bounds these tiny buffers; fallback must not raise them.
    assert _sort_memory(low_bam) == _sort_memory(baseline_bam)
    baseline_records = _samtools("view", baseline_bam)
    assert len(baseline_records.splitlines()) == 2 * pairs
    assert _samtools("view", low_bam) == baseline_records


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize(("memory", "cpu_fits"), [("100M", False), ("384M", True)])
def test_issue747_cpu_fallback_retains_smaller_or_already_fitting_sort_budget(
    tmp_path, bwa_bin, ram, memory, cpu_fits
):
    reads = _short_reads(tmp_path, **_GENOME_SIZED)
    _install_build(bwa_bin, "bwa")
    upstream_bam, _ = _align(
        reads, tmp_path / "off", "--native-aligners", "off", memory=memory
    )
    cpu = _needed(reads, memory) - bwa_gpu_memory(reads.ref, 2 * 140)
    budget = (cpu + _needed(reads, "256M")) // 2 if cpu_fits else cpu - GIB // 4
    ram(2 * GIB + budget)

    bam, result = _align(reads, tmp_path / "out", memory=memory)

    assert _sort_memory(bam) == memory
    assert result.metrics["native_aligner_backend"] == "cpu"
    assert _samtools("view", bam) == _samtools("view", upstream_bam)


@pytest.mark.usefixtures("apple_silicon")
def test_realign_keeps_streaming_when_only_metal_would_not_fit(tmp_path, bwa_bin, ram):
    # #541 review: picking Metal pushed realign off streaming ("Available RAM
    # changed before streaming started") into extracting FASTQs to disk.
    genome = _short_reads(tmp_path / "reads").genome[:6_000]
    (tmp_path / "old").mkdir()
    ref = str(write_fasta(tmp_path / "ref.fa", {"chr1": genome}))
    source, pairs = _old_alignment(tmp_path, genome)
    _install_build(bwa_bin, "bwa")
    reads = 2 * pairs
    upstream = estimate_mapping_memory(
        ref, "bwa", threads="2", memory="100M", samblaster=True, reads=reads,
        platform="illumina", read_length=100,
    ).total  # fmt: skip
    # Room for bwa's pipeline and collation's few reads, but not for the GPU.
    ram(16 * GIB, available=upstream + bwa_gpu_memory(ref, reads) // 2)
    outdir = tmp_path / "out"

    result = _run_realign(source, ref, outdir)

    bam = str(outdir / "x_aligned.bam")
    assert _mapper(bam).startswith("wgse-bwa mem ") and " -p " in _mapper(bam)
    steps = json.loads((outdir / "x_aligned.bam.steps.json").read_text())["steps"]
    assert "extract" not in steps
    assert not list(outdir.glob("*.fastq.gz"))
    assert result.metrics["native_aligner_backend"] == "cpu"
