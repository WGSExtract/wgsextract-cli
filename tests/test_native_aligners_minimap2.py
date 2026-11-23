"""Focused native aligners minimap2 regressions."""

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fake_tool_launchers import expose_tool_fakes, python_tool
from tests.fake_tools import only_tools
from tests.test_alignment_commands import _samtools, _short_reads
from tests.test_native_aligners import (
    GIB,
    _align,
    _align_single,
    _install_build,
    _linux,
    _minimap2_cuda,
    _native,
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
from wgsextract_cli.commands.alignment import backends
from wgsextract_cli.core import native_aligners, windows_arm64
from wgsextract_cli.core.alignment.memory import (
    estimate_mapping_memory,
)
from wgsextract_cli.core.alignment.samtools_commands import sort_memory


@pytest.mark.usefixtures("apple_silicon")
def test_auto_aligns_long_reads_with_wgse_minimap2(tmp_path, request):
    bindir = only_tools(request, "minimap2", "samtools")
    reads = _short_reads(tmp_path)
    _install_build(bindir, "minimap2")

    native_bam, native = _align(reads, tmp_path / "auto", "--aligner", "minimap2")
    upstream_bam, upstream = _align(
        reads, tmp_path / "off", "--aligner", "minimap2", "--native-aligners", "off"
    )

    assert _samtools("view", native_bam) == _samtools("view", upstream_bam)
    assert native.metrics["aligner"] == "minimap2"
    assert _native(native) == {
        "native_aligner": "wgse-minimap2",
        "native_aligner_version": "0.1.0",
        "native_aligner_backend": "cpu",
    }
    assert native.provenance.tools["wgse-minimap2"] == "0.1.0"
    assert _native(upstream) == {}


@pytest.mark.parametrize(
    ("machine", "version", "options", "backend"),
    [
        ("x86_64", "0.4.0", ("--platform", "ont"), "cuda"),
        ("x86_64", "0.4.0", ("--platform", "hifi"), "cuda"),
        # Before 0.4.0 wgse-minimap2 had no --backend.
        ("x86_64", "0.3.0", ("--platform", "ont"), "cpu"),
        # wgse-minimap2 is built for Linux x86-64 only.
        ("aarch64", "0.4.0", ("--platform", "ont"), None),
    ],
)
def test_linux_long_reads_run_wgse_minimap2_on_cuda(
    tmp_path, request, monkeypatch, ram, machine, version, options, backend
):
    _linux(monkeypatch, machine)
    ram(64 * GIB)
    bindir = only_tools(request, "minimap2", "samtools")
    reads = _short_reads(tmp_path)
    _install_build(bindir, "minimap2", _minimap2_cuda(version))

    bam, result = _align_single(reads, tmp_path / "auto", *options)
    upstream_bam, _ = _align_single(
        reads, tmp_path / "off", *options, "--native-aligners", "off"
    )

    if backend is None:
        assert _native(result) == {}
    else:
        assert result.metrics["native_aligner"] == "wgse-minimap2"
        assert result.metrics["native_aligner_backend"] == backend
    assert _samtools("view", bam) == _samtools("view", upstream_bam)
    assert not result.warnings


@pytest.mark.usefixtures("linux_x86_64")
def test_linux_short_reads_keep_wgse_minimap2_on_the_cpu(tmp_path, request):
    # sr's gap fills stay on the CPU even with --cuda, so no GPU is set up.
    bindir = only_tools(request, "minimap2", "samtools")
    reads = _short_reads(tmp_path)
    _install_build(bindir, "minimap2", _minimap2_cuda())

    bam, result = _align(reads, tmp_path / "out", "--aligner", "minimap2")

    assert result.metrics["native_aligner"] == "wgse-minimap2"
    assert result.metrics["native_aligner_backend"] == "cpu"
    assert _samtools("view", "-c", bam) != "0\n"


@pytest.mark.usefixtures("linux_x86_64")
def test_linux_wgse_minimap2_without_ram_for_its_gpu_runs_on_the_cpu(
    tmp_path, request, ram
):
    bindir = only_tools(request, "minimap2", "samtools")
    reads = _short_reads(tmp_path)
    _install_build(bindir, "minimap2", _minimap2_cuda())

    size = Path(reads.r1).stat().st_size

    def needed(gpu_bytes):
        return estimate_mapping_memory(
            reads.ref,
            "minimap2",
            threads="2",
            # #781: the sort gets no more than these reads fill.
            memory=sort_memory("2", "1G", input_bytes=size),
            samblaster=False,
            platform="ont",
            read_length=100,
            preset="map-ont",
            input_bytes=size,
            gpu_bytes=gpu_bytes,
        ).total

    # #752 review: just the CPU backend's estimate, which the GPU backend's
    # (with its RAM's headroom too) exceeds.
    ram(64 * GIB, needed(0))

    bam, result = _align_single(reads, tmp_path / "out", "--platform", "ont")

    assert result.metrics["native_aligner"] == "wgse-minimap2"
    assert result.metrics["native_aligner_backend"] == "cpu"
    assert _samtools("view", "-c", bam) != "0\n"


@pytest.mark.usefixtures("linux_x86_64")
def test_linux_wgse_minimap2_keeps_its_gpu_when_busy_at_the_first_probe(
    tmp_path, request
):
    # #752 review: capabilities are asked once per process, and a GPU busy then
    # reports cuda_available false; --backend auto checks it as each run starts.
    bindir = only_tools(request, "minimap2", "samtools")
    reads = _short_reads(tmp_path)
    _install_build(bindir, "minimap2", _minimap2_cuda(cuda_available=False))

    _, result = _align_single(reads, tmp_path / "out", "--platform", "hifi")

    assert result.metrics["native_aligner_backend"] == "cuda"


@pytest.mark.parametrize(
    ("installed", "cuda_available", "commit_gib", "options", "backend"),
    [
        (True, True, 64, (), "cuda"),
        (True, False, 64, (), "cpu"),
        (True, True, 64, ("--no-prefer-native-gpu",), "cpu"),
        (True, True, 1, ("--prefer-native-gpu",), "cpu"),
        (False, False, 64, (), None),
    ],
)
def test_issue1121_windows_long_reads_select_cuda_or_fall_back(
    tmp_path,
    request,
    monkeypatch,
    ram,
    installed,
    cuda_available,
    commit_gib,
    options,
    backend,
):
    monkeypatch.setattr(
        native_aligners,
        "platform",
        SimpleNamespace(system=lambda: "Windows", machine=lambda: "AMD64"),
    )
    monkeypatch.setattr(windows_arm64, "windows_native_machine", lambda: None)
    monkeypatch.setattr(backends, "windows_available_commit", lambda: commit_gib * GIB)
    ram(64 * GIB)
    bindir = only_tools(request, "minimap2", "samtools", copy_on_windows=True)
    minimap2 = shutil.which("minimap2")
    reads = _short_reads(tmp_path)
    if installed:
        # Stand in for a CUDA-capable executable, but map with real minimap2.
        capability_file = tmp_path / "capabilities.json"
        capability_file.write_text(_minimap2_cuda("0.4.1", cuda_available))
        python_tool(
            bindir,
            "wgse-minimap2",
            f"""
import subprocess, sys
from pathlib import Path
args = sys.argv[1:]
if args[:1] == ["capabilities"]:
    sys.stdout.write(Path({str(capability_file)!r}).read_text())
    sys.exit(0)
if args[:2] == ["--backend", "auto"]:
    args = args[2:]
sys.exit(subprocess.run([{minimap2!r}, *args]).returncode)
""",
        )
        expose_tool_fakes(monkeypatch, bindir, "wgse-minimap2")

    bam, result = _align_single(reads, tmp_path / "auto", "--platform", "ont", *options)
    upstream, _ = _align_single(
        reads, tmp_path / "upstream", "--platform", "ont", "--native-aligners", "off"
    )

    assert result.metrics.get("native_aligner_backend") == backend
    assert _samtools("view", bam) == _samtools("view", upstream)
    assert not result.warnings
    if installed and not cuda_available:
        # A long-lived CLI/API process can recover when another job frees VRAM.
        capability_file.write_text(_minimap2_cuda("0.4.1"))
        retry_bam, retry = _align_single(
            reads, tmp_path / "gpu-now-available", "--platform", "ont"
        )
        assert retry.metrics["native_aligner_backend"] == "cuda"
        assert _samtools("view", retry_bam) == _samtools("view", upstream)
