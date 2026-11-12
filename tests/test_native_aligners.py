"""``align`` and ``realign`` with WGS Extract's Apple Silicon aligner builds
(wgse-bwa, wgse-bwa-mem3, wgse-minimap2) on PATH, and with bwa-mem3.

The builds are faked at their boundary: a ``wgse-bwa`` that answers
``capabilities --json`` and otherwise runs the real bwa under its own name,
as the real build does (its ``@PG`` CL names ``wgse-bwa`` and its backend
option). The fake bwa-mem3 runs the real bwa or bwa-mem2 whose records its
``--compat`` mode writes. Apple Silicon, its GPU and the Mac's RAM are the
mocked platform.
"""

import json
from argparse import Namespace
from pathlib import Path

import pytest

from tests.fake_tools import only_tools, python_tool
from tests.htslib_helpers import run_wgsextract, write_fasta
from tests.test_alignment_commands import _programs, _samtools, _short_reads
from tests.test_realign import _args as _realign_args
from tests.test_realign import _old_alignment
from wgsextract_cli.commands import realign
from wgsextract_cli.core import native_aligners, results
from wgsextract_cli.core.alignment_memory import (
    bwa_gpu_memory,
    estimate_mapping_memory,
)
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.utils import UsageError, WGSExtractError

GIB = 1024**3

_FAKE_BUILD = """\
import os, sys

args = sys.argv[1:]
if args[:1] == ["capabilities"]:
    sys.stdout.write({capabilities!r})
    sys.exit({status})
backend = args[:2] if args[:1] == ["--backend"] else []
os.execv({real!r}, [" ".join([{name!r}, *backend]), *args[len(backend):]])
"""


def _capabilities(tool, **overrides):
    document = {
        "schema_version": 1,
        "version": {
            "bwa": "0.7.1",
            "bwa-mem2": "0.5.0",
            "bwa-mem3": "0.2.0",
            "minimap2": "0.1.0",
        }[tool],
        "engine": tool,
        "engine_version": {
            "bwa": "0.7.19",
            "bwa-mem2": "2.3",
            "bwa-mem3": "0.16.0",
            "minimap2": "2.31-r1302",
        }[tool],
        "qualified": False,
        "metal_available": tool != "minimap2",
        "metal_hardware_supported": tool != "minimap2",
        "cpu_available": True,
    }
    document.update(overrides)
    return json.dumps(document) + "\n"


def _install_build(bindir, engine, capabilities=None, status=0, real=None):
    """A wgse-<engine> in bindir running the real ``real`` (else ``engine``)."""
    name = f"wgse-{engine}"
    source = _FAKE_BUILD.format(
        capabilities=_capabilities(engine) if capabilities is None else capabilities,
        status=status,
        real=str((bindir / (real or engine)).resolve()),
        name=name,
    )
    return python_tool(bindir, name, source)


@pytest.fixture
def apple_silicon(monkeypatch, ram):
    """An Apple Silicon Mac with 64 GiB of RAM."""
    monkeypatch.setattr(
        native_aligners,
        "platform",
        Namespace(system=lambda: "Darwin", machine=lambda: "arm64"),
    )
    ram(64 * GIB)


@pytest.fixture
def linux_x86_64(monkeypatch, ram):
    """A Linux x86-64 computer with 64 GiB of RAM."""
    monkeypatch.setattr(
        native_aligners,
        "platform",
        Namespace(system=lambda: "Linux", machine=lambda: "x86_64"),
    )
    ram(64 * GIB)


def _cuda_capabilities(tool, cuda_available=True):
    """A Linux build's capabilities (#674): CUDA, never Metal."""
    return _capabilities(
        tool,
        version="0.10.0",
        metal_available=False,
        metal_hardware_supported=False,
        cuda_available=cuda_available,
        cuda_hardware_supported=cuda_available,
    )


@pytest.fixture
def bwa_bin(request):
    return only_tools(request, "bwa", "samtools")


def _run(*argv):
    """Run a command line; its result (metrics, warnings, tools)."""
    result = results.CommandResult(command=argv[0])
    with results.collecting(result):
        run_wgsextract(*argv)
    result.finalize()
    return result


def _align(reads, out, *options, memory="1G"):
    result = _run(
        "align", "--r1", reads.r1, "--r2", reads.r2, "--ref", reads.ref,
        "--outdir", str(out), "--threads", "2", "--memory", memory, *options,
    )  # fmt: skip
    (bam,) = out.glob("*_aligned.bam")
    return str(bam), result


def _mapper(bam, program="bwa"):
    """The CL of the alignment's mapper's @PG line."""
    (line,) = [p for p in _programs(bam) if f"\tID:{program}\t" in p]
    return line.split("\tCL:")[1]


def _sort_memory(bam):
    (line,) = [p for p in _programs(bam) if "CL:samtools sort" in p]
    words = line.split("\tCL:")[1].split()
    return words[words.index("-m") + 1]


def _run_realign(source, ref, outdir, **options):
    result = results.CommandResult(command="realign")
    with results.collecting(result):
        realign.run(_realign_args(source, ref=ref, outdir=str(outdir), **options))
    result.finalize()
    return result


def _native(result):
    return {k: v for k, v in result.metrics.items() if k.startswith("native_")}


@pytest.mark.usefixtures("apple_silicon")
def test_auto_aligns_with_wgse_bwa_on_metal_and_names_it(tmp_path, bwa_bin):
    reads = _short_reads(tmp_path)
    _install_build(bwa_bin, "bwa")

    native_bam, native = _align(reads, tmp_path / "auto")
    upstream_bam, upstream = _align(reads, tmp_path / "off", "--native-aligners", "off")

    assert _mapper(native_bam).startswith("wgse-bwa --backend auto mem ")
    assert _mapper(upstream_bam).startswith("bwa mem ")
    assert _samtools("view", native_bam) == _samtools("view", upstream_bam)
    assert native.metrics["aligner"] == upstream.metrics["aligner"] == "bwa"
    assert _native(native) == {
        "native_aligner": "wgse-bwa",
        "native_aligner_version": "0.7.1",
        "native_aligner_backend": "metal",
    }
    assert native.provenance.tools["wgse-bwa"] == "0.7.1"
    assert _native(upstream) == {} and "wgse-bwa" not in upstream.provenance.tools
    assert not native.warnings


@pytest.mark.usefixtures("linux_x86_64")
def test_linux_aligns_with_wgse_bwa_on_cuda_and_names_it(tmp_path, bwa_bin):
    # #674: wgse-bwa's CUDA backend, used as the Metal one is.
    reads = _short_reads(tmp_path)
    _install_build(bwa_bin, "bwa", _cuda_capabilities("bwa"))

    native_bam, native = _align(reads, tmp_path / "auto")
    upstream_bam, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")

    assert _mapper(native_bam).startswith("wgse-bwa --backend auto mem ")
    assert _samtools("view", native_bam) == _samtools("view", upstream_bam)
    assert _native(native) == {
        "native_aligner": "wgse-bwa",
        "native_aligner_version": "0.10.0",
        "native_aligner_backend": "cuda",
    }
    assert not native.warnings


@pytest.mark.usefixtures("linux_x86_64")
def test_linux_without_a_usable_nvidia_gpu_runs_wgse_bwa_on_the_cpu(tmp_path, bwa_bin):
    reads = _short_reads(tmp_path)
    _install_build(bwa_bin, "bwa", _cuda_capabilities("bwa", cuda_available=False))

    bam, result = _align(reads, tmp_path / "out")

    assert _mapper(bam).startswith("wgse-bwa mem ")
    assert result.metrics["native_aligner_backend"] == "cpu"


def test_linux_arm64_aligns_with_wgse_bwa_on_the_cpu(
    tmp_path, bwa_bin, monkeypatch, ram
):
    # #672: the linux-aarch64 build has no CUDA backend; its CPU backend's NEON
    # steps write bwa's exact records.
    monkeypatch.setattr(
        native_aligners,
        "platform",
        Namespace(system=lambda: "Linux", machine=lambda: "aarch64"),
    )
    ram(64 * GIB)
    reads = _short_reads(tmp_path)
    _install_build(bwa_bin, "bwa", _cuda_capabilities("bwa", cuda_available=False))

    native_bam, native = _align(reads, tmp_path / "auto")
    upstream_bam, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")

    assert _mapper(native_bam).startswith("wgse-bwa mem ")
    assert _samtools("view", native_bam) == _samtools("view", upstream_bam)
    assert _native(native) == {
        "native_aligner": "wgse-bwa",
        "native_aligner_version": "0.10.0",
        "native_aligner_backend": "cpu",
    }
    assert not native.warnings


@pytest.mark.usefixtures("linux_x86_64")
def test_linux_uses_no_apple_silicon_only_build(bwa_bin):
    # Only wgse-bwa is built for Linux; a wgse-bwa-mem2 on PATH is not used.
    _install_build(bwa_bin, "bwa-mem2", _cuda_capabilities("bwa-mem2"), real="bwa")

    assert native_aligners.find_native_aligner("bwa-mem2", "auto") is None


@pytest.mark.usefixtures("apple_silicon")
def test_config_turns_native_aligners_off(tmp_path, bwa_bin, monkeypatch):
    reads = _short_reads(tmp_path)
    _install_build(bwa_bin, "bwa")
    monkeypatch.setitem(settings, "native_aligners", "off")

    bam, result = _align(reads, tmp_path / "out")

    assert _mapper(bam).startswith("bwa mem ")
    assert _native(result) == {}


def test_a_bad_native_aligners_setting_is_refused(tmp_path, monkeypatch):
    reads = _short_reads(tmp_path)
    monkeypatch.setitem(settings, "native_aligners", "always")

    with pytest.raises(UsageError, match="must be one of auto, off"):
        _align(reads, tmp_path / "out")


@pytest.mark.parametrize("machine", [("Darwin", "x86_64"), ("Windows", "AMD64")])
def test_other_computers_align_with_upstream_bwa(
    tmp_path, bwa_bin, monkeypatch, machine
):
    reads = _short_reads(tmp_path)
    _install_build(bwa_bin, "bwa")
    system, arch = machine
    monkeypatch.setattr(
        native_aligners,
        "platform",
        Namespace(system=lambda: system, machine=lambda: arch),
    )

    bam, result = _align(reads, tmp_path / "out")

    assert _mapper(bam).startswith("bwa mem ")
    assert _native(result) == {} and not result.warnings


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize(
    ("capabilities", "status", "reason"),
    [
        ("", 1, "exited with 1"),
        ("wgse-bwa 0.7.1\n", 0, "did not print JSON"),
        ('["bwa"]\n', 0, "did not print an object"),
        (_capabilities("bwa", schema_version=2), 0, "schema_version 2"),
        (_capabilities("bwa", version="0.6.0"), 0, "older than the required 0.7.0"),
        (_capabilities("bwa", version="0.8.0-rc1"), 0, "older than the required"),
        (_capabilities("bwa", engine="bwa-mem2"), 0, "engine 'bwa-mem2'"),
        (_capabilities("bwa", cpu_available=False), 0, "no CPU backend"),
    ],
)
def test_an_unusable_build_warns_and_upstream_bwa_aligns(
    tmp_path, bwa_bin, capabilities, status, reason
):
    reads = _short_reads(tmp_path)
    build = _install_build(bwa_bin, "bwa", capabilities, status)

    bam, result = _align(reads, tmp_path / "out")

    assert _mapper(bam).startswith("bwa mem ")
    assert _native(result) == {}
    (warning,) = [w for w in result.warnings if w.code == "native_aligner_unusable"]
    assert reason in warning.message
    assert warning.details == {
        "binary": "wgse-bwa",
        "path": str(build),
        "aligner": "bwa",
    }


@pytest.mark.usefixtures("apple_silicon")
def test_a_broken_build_is_asked_once_per_process_until_replaced(tmp_path, bwa_bin):
    # #541 review: a hung or broken build was probed again on every run in a
    # long-lived process (the app), each time up to the 30 s timeout.
    reads = _short_reads(tmp_path)
    asked = tmp_path / "asked"
    broken = f"import sys\nopen({str(asked)!r}, 'a').write('x')\nsys.exit(1)\n"
    python_tool(bwa_bin, "wgse-bwa", broken)

    runs = [_align(reads, tmp_path / f"run{i}")[1] for i in range(2)]
    asked_before_replacing = asked.read_text()
    python_tool(bwa_bin, "wgse-bwa", broken + "# a new build\n")
    _align(reads, tmp_path / "replaced")

    assert asked_before_replacing == "x"
    assert asked.read_text() == "xx"
    for result in runs:
        assert [w.code for w in result.warnings] == ["native_aligner_unusable"]


@pytest.mark.usefixtures("apple_silicon")
def test_wgse_bwa_without_metal_aligns_on_the_cpu(tmp_path, bwa_bin):
    reads = _short_reads(tmp_path)
    _install_build(bwa_bin, "bwa", _capabilities("bwa", metal_available=False))

    bam, result = _align(reads, tmp_path / "out")

    assert _mapper(bam).startswith("wgse-bwa mem ")
    assert result.metrics["native_aligner_backend"] == "cpu"


def _needed(reads, sort_memory):
    """The pipeline's memory estimate with wgse-bwa's GPU backend."""
    return estimate_mapping_memory(
        reads.ref,
        "bwa",
        threads="2",
        memory=sort_memory,
        samblaster=False,
        reads=2 * 140,
        read_length=100,
        gpu_bytes=bwa_gpu_memory(reads.ref, 2 * 140),
    ).total


@pytest.mark.usefixtures("apple_silicon")
def test_a_mac_short_of_ram_for_metal_sorts_in_less_memory(tmp_path, bwa_bin, ram):
    # #480: a 16 GB Mac. Sorting with less memory leaves the output unchanged;
    # the batch size (-K) never changes.
    reads = _short_reads(tmp_path)
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
    reads = _short_reads(tmp_path)
    _install_build(bwa_bin, "bwa")
    ram(2 * GIB + _needed(reads, "256M") - GIB // 4)

    bam, result = _align(reads, tmp_path / "out", memory="256M")

    assert _mapper(bam).startswith("wgse-bwa mem -K 100000000 ")
    assert _sort_memory(bam) == "256M"
    assert result.metrics["native_aligner_backend"] == "cpu"


@pytest.mark.usefixtures("apple_silicon")
def test_a_genome_too_big_for_the_macs_ram_still_aligns_on_metal(
    tmp_path, bwa_bin, ram
):
    # Issue #549: a 30x genome on a 16 GB M1 was estimated over RAM with or without
    # the GPU, so wgse-bwa ran on the CPU, about 4x slower. Metal with less
    # sort memory needs less than the CPU run with the default.
    reads = _short_reads(tmp_path)
    _install_build(bwa_bin, "bwa")
    upstream_bam, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")
    ram(2 * GIB + _needed(reads, "256M") - GIB)

    bam, result = _align(reads, tmp_path / "out")

    assert _mapper(bam).startswith("wgse-bwa --backend auto mem -K 100000000 ")
    assert _sort_memory(bam) == "512M"
    assert _samtools("view", bam) == _samtools("view", upstream_bam)
    assert result.metrics["native_aligner_backend"] == "metal"


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


@pytest.mark.usefixtures("apple_silicon")
def test_realign_streams_into_wgse_bwa_and_a_resumed_run_still_names_it(
    tmp_path, bwa_bin
):
    genome = _short_reads(tmp_path / "reads").genome[:6_000]
    (tmp_path / "old").mkdir()
    ref = str(write_fasta(tmp_path / "ref.fa", {"chr1": genome}))
    source, _ = _old_alignment(tmp_path, genome)
    _install_build(bwa_bin, "bwa")
    outdir = tmp_path / "out"

    def realign_once():
        return _run_realign(source, ref, outdir)

    first = realign_once()
    bam = outdir / "x_aligned.bam"
    written = bam.stat().st_mtime_ns
    # #541 review: the resumed manifest names the build that aligned, not a
    # newer one installed since.
    _install_build(bwa_bin, "bwa", _capabilities("bwa", version="0.7.2"))
    resumed = realign_once()

    assert _mapper(str(bam)).startswith("wgse-bwa --backend auto mem ")
    assert " -p " in _mapper(str(bam))
    assert bam.stat().st_mtime_ns == written
    assert (
        _native(first)
        == _native(resumed)
        == {
            "native_aligner": "wgse-bwa",
            "native_aligner_version": "0.7.1",
            "native_aligner_backend": "metal",
        }
    )
    assert first.provenance.tools["wgse-bwa"] == "0.7.1"
    assert resumed.provenance.tools["wgse-bwa"] == "0.7.1"


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


# bwa-mem3 (#542): a fake that runs the real aligner whose records its
# --compat mode writes (bwa for --fast, whose own records differ). Linux's
# bwa-mem2 finds its SIMD build from its argv[0], so the aligner keeps its own
# name and the fake writes its command line into the @PG CL instead.
_FAKE_BWA_MEM3 = """\
import subprocess, sys

args = sys.argv[1:]
if args[:1] == ["capabilities"]:
    sys.stdout.write({capabilities!r})
    sys.exit(0)
if args[:1] == ["version"]:
    sys.exit(print("0.16.0"))
backend = args[:2] if args[:1] == ["--backend"] else []
mem, mode, *rest = args[len(backend):]
real = {real!r}["bwa-mem2" if mode == "--compat=bwa-mem2" else "bwa"]
command_line = " ".join([{name!r}, *backend, mem, mode])
aligner = subprocess.Popen([real, mem, *rest], stdout=subprocess.PIPE)
for line in aligner.stdout:
    if line.startswith(b"@PG"):
        line = line.replace(b"CL:" + real.encode(), b"CL:" + command_line.encode(), 1)
    sys.stdout.buffer.write(line)
sys.exit(aligner.wait())
"""


def _install_bwa_mem3(
    bindir, name="wgse-bwa-mem3", capabilities=None, mem2="bwa-mem2", real_bin=None
):
    """The fake; ``mem2`` names the real aligner for --compat=bwa-mem2 (bwa
    when the test's bwa-mem2 index can't be loaded), in ``real_bin`` (else
    ``bindir``)."""
    real_bin = real_bin or bindir
    real = {"bwa": str((real_bin / "bwa").resolve())}
    real["bwa-mem2"] = str((real_bin / mem2).resolve())
    source = _FAKE_BWA_MEM3.format(
        capabilities=capabilities or _capabilities("bwa-mem3"), real=real, name=name
    )
    return python_tool(bindir, name, source)


@pytest.fixture
def mem3_bin(request):
    return only_tools(request, "bwa", "bwa-mem2", "samtools")


def _mem3_reads(tmp_path):
    """Reads whose reference has bwa's index and bwa-mem2's, which bwa-mem3
    maps with."""
    reads = _short_reads(tmp_path)
    for indexer in (["bwa", "index"], ["bwa-mem2", "index"]):
        _run_tool([*indexer, reads.ref])
    return reads


def _run_tool(argv):
    from wgsextract_cli.core.utils import run_command

    run_command(argv, capture_output=True)


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize(
    ("aligner", "compat"), [("bwa", "bwa-mem"), ("bwa-mem2", "bwa-mem2")]
)
def test_wgse_bwa_mem3_stands_in_with_the_requested_aligners_records(
    tmp_path, mem3_bin, aligner, compat
):
    reads = _mem3_reads(tmp_path)
    _install_bwa_mem3(mem3_bin)

    native_bam, native = _align(reads, tmp_path / "auto", "--aligner", aligner)
    upstream_bam, _ = _align(
        reads, tmp_path / "off", "--aligner", aligner, "--native-aligners", "off"
    )

    # contract: --compat=<target> is what makes bwa-mem3 write the target's records.
    assert _mapper(native_bam, aligner).startswith(
        f"wgse-bwa-mem3 --backend auto mem --compat={compat} "
    )
    assert _samtools("view", native_bam) == _samtools("view", upstream_bam)
    assert native.metrics["aligner"] == aligner
    assert native.metrics["bwa_mem3_mode"] == f"compat={compat}"
    assert _native(native) == {
        "native_aligner": "wgse-bwa-mem3",
        "native_aligner_version": "0.2.0",
        "native_aligner_backend": "metal",
    }
    assert native.provenance.tools["wgse-bwa-mem3"] == "0.2.0"
    assert not native.warnings


@pytest.mark.usefixtures("apple_silicon")
def test_without_bwa_mem2s_index_wgse_bwa_aligns_in_bwas_place(tmp_path, mem3_bin):
    reads = _short_reads(tmp_path)
    _install_bwa_mem3(mem3_bin)
    _install_build(mem3_bin, "bwa")

    bam, result = _align(reads, tmp_path / "out", "--aligner", "bwa")

    assert _mapper(bam).startswith("wgse-bwa --backend auto mem ")
    assert result.metrics["native_aligner"] == "wgse-bwa"
    assert "bwa_mem3_mode" not in result.metrics


@pytest.mark.usefixtures("apple_silicon")
def test_a_mac_short_of_ram_for_bwa_mem3_aligns_with_bwa(tmp_path, mem3_bin, ram):
    # bwa-mem3's index takes about twice bwa's memory: on a Mac where only
    # bwa's pipeline fits, bwa aligns.
    reads = _mem3_reads(tmp_path)
    _install_bwa_mem3(mem3_bin)

    def needed(engine, gpu_bytes=0):
        return estimate_mapping_memory(
            reads.ref, engine, threads="2", memory="1G", samblaster=False,
            reads=2 * 140, read_length=100, gpu_bytes=gpu_bytes,
        ).total  # fmt: skip

    ram(2 * GIB + (needed("bwa") + needed("bwa-mem3", GIB // 4)) // 2)

    bam, result = _align(reads, tmp_path / "out", "--aligner", "bwa")

    assert _mapper(bam).startswith("bwa mem ")
    assert _native(result) == {} and not result.warnings


@pytest.mark.usefixtures("apple_silicon")
def test_an_unusable_wgse_bwa_mem3_warns_and_bwa_aligns(tmp_path, mem3_bin):
    reads = _mem3_reads(tmp_path)
    build = _install_bwa_mem3(
        mem3_bin, capabilities=_capabilities("bwa-mem3", version="0.1.0")
    )

    bam, result = _align(reads, tmp_path / "out", "--aligner", "bwa")

    assert _mapper(bam).startswith("bwa mem ")
    (warning,) = result.warnings
    assert warning.code == "native_aligner_unusable"
    assert warning.details == {
        "binary": "wgse-bwa-mem3",
        "path": str(build),
        "aligner": "bwa",
    }


@pytest.mark.usefixtures("apple_silicon")
def test_aligner_bwa_mem3_writes_bwas_records(tmp_path, mem3_bin):
    reads = _mem3_reads(tmp_path)
    _install_bwa_mem3(mem3_bin)

    bam, result = _align(reads, tmp_path / "mem3", "--aligner", "bwa-mem3")
    bwa_bam, _ = _align(
        reads, tmp_path / "bwa", "--aligner", "bwa", "--native-aligners", "off"
    )

    # contract: bwa-mem3 stands for bwa's exact records unless --fast.
    assert _mapper(bam).startswith("wgse-bwa-mem3 --backend auto mem --compat=bwa-mem ")
    assert _samtools("view", bam) == _samtools("view", bwa_bam)
    assert result.metrics["aligner"] == "bwa-mem3"
    assert result.metrics["bwa_mem3_mode"] == "compat=bwa-mem"
    assert not result.warnings


@pytest.mark.parametrize(
    ("machine", "options"),
    [(("Linux", "x86_64"), ()), (("Darwin", "arm64"), ("--native-aligners", "off"))],
)
def test_fast_aligns_with_bwa_mem3s_preset_and_warns(
    tmp_path, mem3_bin, monkeypatch, ram, machine, options
):
    # Off Apple Silicon, or with native aligners off, bwa-mem3 from PATH runs
    # even with wgse-bwa-mem3 installed.
    reads = _mem3_reads(tmp_path)
    _install_bwa_mem3(mem3_bin)
    _install_bwa_mem3(mem3_bin, name="bwa-mem3")
    system, arch = machine
    monkeypatch.setattr(
        native_aligners,
        "platform",
        Namespace(system=lambda: system, machine=lambda: arch),
    )
    ram(64 * GIB)

    bam, result = _align(reads, tmp_path / "out", "--fast", *options)

    # contract: --fast is the opt-in that trades BWA's alignments for speed.
    assert _mapper(bam).startswith("bwa-mem3 mem --fast ")
    assert result.metrics["aligner"] == "bwa-mem3"
    assert result.metrics["bwa_mem3_mode"] == "fast"
    assert _native(result) == {}
    assert result.provenance.tools["bwa-mem3"] == "0.16.0"
    assert [w.code for w in result.warnings] == ["bwa_mem3_fast"]


@pytest.mark.parametrize(
    "options",
    [
        ("--aligner", "bwa", "--fast"),
        ("--aligner", "minimap2", "--fast"),
        ("--long-read", "--fast"),
        ("--gpu", "--fast"),
        ("--platform", "ultima", "--fast"),
    ],
)
def test_fast_is_refused_for_other_aligners(tmp_path, options):
    reads = _short_reads(tmp_path)

    with pytest.raises(UsageError, match="--fast"):
        _align(reads, tmp_path / "out", *options)


@pytest.mark.parametrize(
    ("options", "advice"),
    [
        (("--aligner", "bwa-mem3"), "Or align with --aligner auto."),
        (("--fast",), "Or drop --fast."),
        (("--aligner", "bwa-mem3", "--fast"), "--aligner auto without --fast."),
    ],
)
def test_bwa_mem3_without_bwa_mem3_fails_clearly(tmp_path, mem3_bin, options, advice):
    reads = _mem3_reads(tmp_path)

    with pytest.raises(WGSExtractError) as raised:
        _align(reads, tmp_path / "out", *options)

    message = str(raised.value)
    assert "needs wgse-bwa-mem3 (Apple Silicon) or bwa-mem3" in message
    assert message.endswith(advice)


@pytest.mark.usefixtures("apple_silicon")
def test_native_aligners_off_names_the_installed_wgse_bwa_mem3(tmp_path, mem3_bin):
    # #590 review: the error told the user to install what they already had.
    reads = _mem3_reads(tmp_path)
    _install_bwa_mem3(mem3_bin)

    with pytest.raises(WGSExtractError, match="native_aligners is off"):
        _align(reads, tmp_path / "out", "--fast", "--native-aligners", "off")


@pytest.mark.usefixtures("apple_silicon")
def test_aligner_bwa_mem3_without_bwa_mem2s_index_fails_clearly(tmp_path, mem3_bin):
    reads = _short_reads(tmp_path)
    _install_bwa_mem3(mem3_bin)

    with pytest.raises(UsageError, match="ref index --aligner bwa-mem2"):
        _align(reads, tmp_path / "out", "--aligner", "bwa-mem3")


@pytest.mark.usefixtures("apple_silicon")
def test_realign_fast_streams_into_bwa_mem3_and_a_resumed_run_still_warns(
    tmp_path, mem3_bin
):
    genome = _short_reads(tmp_path / "reads").genome[:6_000]
    (tmp_path / "old").mkdir()
    ref = str(write_fasta(tmp_path / "ref.fa", {"chr1": genome}))
    for indexer in (["bwa", "index"], ["bwa-mem2", "index"]):
        _run_tool([*indexer, ref])
    source, _ = _old_alignment(tmp_path, genome)
    _install_bwa_mem3(mem3_bin)
    outdir = tmp_path / "out"

    first = _run_realign(source, ref, outdir, fast=True)
    bam = outdir / "x_aligned.bam"
    written = bam.stat().st_mtime_ns
    resumed = _run_realign(source, ref, outdir, fast=True)

    assert _mapper(str(bam)).startswith("wgse-bwa-mem3 --backend auto mem --fast ")
    assert " -p " in _mapper(str(bam))
    assert bam.stat().st_mtime_ns == written
    for result in (first, resumed):
        assert result.metrics["bwa_mem3_mode"] == "fast"
        assert result.metrics["native_aligner"] == "wgse-bwa-mem3"
        assert [w.code for w in result.warnings] == ["bwa_mem3_fast"]


def _bloat_0123(ref):
    """Make bwa-mem2's .0123 file a sparse 64 GiB (so bwa-mem2 can't load it)."""
    with open(ref + ".0123", "r+b") as index:
        index.truncate(64 * GIB)


def _mem3_needed(reads, engine, available_reads=2 * 140, **options):
    """The pipeline's memory estimate with ``engine`` (as _align runs it)."""
    return estimate_mapping_memory(
        reads.ref, engine, threads="2", samblaster=False, reads=available_reads,
        read_length=100, **{"memory": "1G", **options},
    ).total  # fmt: skip


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("aligner", ["bwa", "auto"])
def test_bwa_aligns_when_only_its_pipeline_fits_the_ram_available(
    tmp_path, mem3_bin, ram, aligner
):
    # #590 review: the stand-in for bwa was checked against total RAM, so with
    # too little available it took bwa's place (and auto, having dropped
    # bwa-mem2 for low available RAM, picked the bigger stand-in). A sparse
    # .0123 makes bwa-mem2's index hs38-sized next to bwa-mem3's.
    reads = _mem3_reads(tmp_path)
    _bloat_0123(reads.ref)
    _install_bwa_mem3(mem3_bin)
    bwa = _mem3_needed(reads, "bwa")
    stand_in = _mem3_needed(reads, "bwa-mem3", gpu_bytes=GIB // 4)
    ram(64 * GIB, available=(bwa + stand_in) // 2)

    bam, result = _align(reads, tmp_path / "out", "--aligner", aligner)

    assert _mapper(bam).startswith("bwa mem ")
    assert "bwa_mem3_mode" not in result.metrics


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("command", ["align", "realign"])
def test_explicit_bwa_mem2_still_stops_when_its_stand_in_does_not_fit(
    tmp_path, mem3_bin, ram, command
):
    # #590 review: with wgse-bwa-mem3 standing in, --aligner bwa-mem2 lost its
    # hard memory check in align (realign still stopped).
    reads = _mem3_reads(tmp_path)
    _install_bwa_mem3(mem3_bin)
    ram(64 * GIB, available=GIB)
    outdir = tmp_path / "out"

    with pytest.raises(WGSExtractError) as raised:
        if command == "align":
            _align(reads, outdir, "--aligner", "bwa-mem2")
        else:
            (tmp_path / "old").mkdir()
            source, _ = _old_alignment(tmp_path, reads.genome[:6_000])
            _run_realign(source, reads.ref, outdir, aligner="bwa-mem2")

    message = str(raised.value)
    assert "wgse-bwa-mem3 (in place of bwa-mem2) alignment is estimated" in message
    assert "Use --aligner bwa" in message
    assert not list(outdir.glob("*.bam")) and not list(outdir.glob("*.fastq.gz"))


@pytest.mark.usefixtures("apple_silicon")
def test_the_stand_in_takes_bwa_mem2s_place_when_it_needs_less(tmp_path, mem3_bin, ram):
    # A reference whose bwa-mem2 index (.0123) is far bigger than bwa-mem3
    # needs, as hs38's is: on a Mac whose RAM less headroom is below the
    # stand-in's estimate, the stand-in still beats bwa-mem2. The .0123 is
    # sparse, so the fake writes bwa-mem2's records with bwa.
    reads = _mem3_reads(tmp_path)
    _install_bwa_mem3(mem3_bin, mem2="bwa")
    _bloat_0123(reads.ref)
    stand_in = _mem3_needed(reads, "bwa-mem3", memory="256M", gpu_bytes=GIB // 4)
    ram(stand_in + GIB)

    bam, result = _align(reads, tmp_path / "out", "--aligner", "bwa-mem2")

    assert _mapper(bam, "bwa").startswith(
        "wgse-bwa-mem3 --backend auto mem --compat=bwa-mem2 "
    )
    assert result.metrics["bwa_mem3_mode"] == "compat=bwa-mem2"


def _mem3_realign_source(tmp_path):
    genome = _short_reads(tmp_path / "reads").genome[:6_000]
    (tmp_path / "old").mkdir()
    ref = str(write_fasta(tmp_path / "ref.fa", {"chr1": genome}))
    for indexer in (["bwa", "index"], ["bwa-mem2", "index"]):
        _run_tool([*indexer, ref])
    source, _ = _old_alignment(tmp_path, genome)
    return source, ref


@pytest.mark.usefixtures("apple_silicon")
def test_realign_streams_into_the_stand_in_with_bwa_mem2s_records(tmp_path, mem3_bin):
    source, ref = _mem3_realign_source(tmp_path)
    _install_bwa_mem3(mem3_bin)

    result = _run_realign(source, ref, tmp_path / "out")
    _run_realign(source, ref, tmp_path / "off", native_aligners="off")

    bam = str(tmp_path / "out" / "x_aligned.bam")
    assert _mapper(bam, "bwa-mem2").startswith(
        "wgse-bwa-mem3 --backend auto mem --compat=bwa-mem2 "
    )
    assert " -p " in _mapper(bam, "bwa-mem2")
    steps = json.loads((tmp_path / "out" / "x_aligned.bam.steps.json").read_text())
    assert "extract" not in steps["steps"]
    assert _samtools("view", bam) == _samtools(
        "view", str(tmp_path / "off" / "x_aligned.bam")
    )
    assert result.metrics["bwa_mem3_mode"] == "compat=bwa-mem2"


@pytest.mark.usefixtures("apple_silicon")
def test_realign_bwa_mem2_with_the_stand_in_needs_no_0123_file(tmp_path, mem3_bin):
    # #590 review: the stand-in maps without bwa-mem2's .0123 file, so realign
    # neither requires nor builds it (it refused before extraction when the
    # build wouldn't fit the RAM).
    source, ref = _mem3_realign_source(tmp_path)
    Path(ref + ".0123").unlink()
    _install_bwa_mem3(mem3_bin, mem2="bwa")

    result = _run_realign(source, ref, tmp_path / "out", aligner="bwa-mem2")

    assert result.metrics["bwa_mem3_mode"] == "compat=bwa-mem2"
    assert (tmp_path / "out" / "x_aligned.bam").is_file()
    assert not Path(ref + ".0123").exists()


@pytest.mark.usefixtures("apple_silicon")
def test_realign_bwa_mem3_warns_once_about_an_unusable_build(tmp_path, mem3_bin):
    # #590 review: the check before extraction and the alignment each warned.
    source, ref = _mem3_realign_source(tmp_path)
    _install_bwa_mem3(mem3_bin, capabilities=_capabilities("bwa-mem3", version="0.1.0"))
    _install_bwa_mem3(mem3_bin, name="bwa-mem3")

    result = _run_realign(source, ref, tmp_path / "out", aligner="bwa-mem3")

    bam = str(tmp_path / "out" / "x_aligned.bam")
    assert _mapper(bam, "bwa").startswith("bwa-mem3 mem --compat=bwa-mem ")
    assert [w.code for w in result.warnings] == ["native_aligner_unusable"]


def _install_wgse_bwa_mem2(bindir, version):
    """wgse-bwa-mem2 ``version``, writing bwa's records with bwa: the test's
    .0123 file is missing or too big for upstream bwa-mem2 to load."""
    capabilities = _capabilities("bwa-mem2", version=version, metal_available=False)
    _install_build(bindir, "bwa-mem2", capabilities, real="bwa")


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("command", ["align", "realign"])
@pytest.mark.parametrize(("version", "fits"), [("0.5.0", True), ("0.4.1", False)])
def test_wgse_bwa_mem2_from_0_5_0_is_not_budgeted_its_0123_file(
    tmp_path, mem3_bin, ram, caplog, command, version, fits
):
    # #595: wgse-bwa-mem2 0.5.0 maps without the .0123 file, but --aligner
    # bwa-mem2's memory gate counted it (6.2 GB for hs38) and refused to run.
    if command == "align":
        reads = _mem3_reads(tmp_path)
        ref = reads.ref
    else:
        source, ref = _mem3_realign_source(tmp_path)
        reads = Namespace(ref=ref)
    _bloat_0123(ref)
    _install_wgse_bwa_mem2(mem3_bin, version)
    without_0123 = _mem3_needed(reads, "wgse-bwa-mem2", memory="256M")
    ram(64 * GIB, available=without_0123 + 2 * GIB)
    outdir = tmp_path / "out"
    caplog.set_level("INFO")

    def run():
        if command == "align":
            return _align(reads, outdir, "--aligner", "bwa-mem2")[1]
        return _run_realign(source, ref, outdir, aligner="bwa-mem2")

    if not fits:
        with pytest.raises(WGSExtractError, match="bwa-mem2 alignment is estimated"):
            run()
        assert not list(outdir.glob("*.bam"))
        return
    result = run()

    assert result.metrics["native_aligner"] == "wgse-bwa-mem2"
    assert result.metrics["native_aligner_version"] == "0.5.0"
    assert "wgse-bwa-mem2 alignment memory estimate" in caplog.text


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("command", ["align", "realign"])
def test_wgse_bwa_mem2_from_0_5_0_needs_no_0123_file(tmp_path, mem3_bin, command):
    # #595: neither wgse-bwa-mem2 0.5.0 backend reads the .0123 file, so it
    # is neither required nor built.
    if command == "align":
        reads = _mem3_reads(tmp_path)
        ref = reads.ref
    else:
        source, ref = _mem3_realign_source(tmp_path)
    Path(ref + ".0123").unlink()
    _install_wgse_bwa_mem2(mem3_bin, "0.5.0")
    outdir = tmp_path / "out"

    if command == "align":
        _, result = _align(reads, outdir, "--aligner", "bwa-mem2")
    else:
        result = _run_realign(source, ref, outdir, aligner="bwa-mem2")

    assert result.metrics["native_aligner"] == "wgse-bwa-mem2"
    assert list(outdir.glob("*_aligned.bam"))
    assert not Path(ref + ".0123").exists()


@pytest.mark.usefixtures("apple_silicon")
def test_wgse_bwa_mem2_0_5_0_aligns_when_the_stand_in_needs_more(
    tmp_path, mem3_bin, ram
):
    # #595: the stand-in was compared with bwa-mem2's estimate including the
    # .0123 file, so it took the place of a wgse-bwa-mem2 that needs less.
    reads = _mem3_reads(tmp_path)
    _install_bwa_mem3(mem3_bin, mem2="bwa")
    _install_wgse_bwa_mem2(mem3_bin, "0.5.0")
    _bloat_0123(reads.ref)
    stand_in = _mem3_needed(reads, "bwa-mem3", memory="256M", gpu_bytes=GIB // 4)
    assert _mem3_needed(reads, "wgse-bwa-mem2", memory="256M") < stand_in - GIB
    ram(stand_in + GIB)

    bam, result = _align(reads, tmp_path / "out", "--aligner", "bwa-mem2")

    assert _mapper(bam, "bwa").startswith("wgse-bwa-mem2 mem ")
    assert "bwa_mem3_mode" not in result.metrics


@pytest.mark.usefixtures("apple_silicon")
def test_native_aligners_off_still_budgets_bwa_mem2s_0123_file(tmp_path, mem3_bin, ram):
    # #658 review: with wgse-bwa-mem2 0.5.0 installed but turned off, upstream
    # bwa-mem2 runs, which loads the .0123 file, so the gate still counts it.
    reads = _mem3_reads(tmp_path)
    _bloat_0123(reads.ref)
    _install_wgse_bwa_mem2(mem3_bin, "0.5.0")
    without_0123 = _mem3_needed(reads, "wgse-bwa-mem2", memory="256M")
    ram(64 * GIB, available=without_0123 + 2 * GIB)

    with pytest.raises(WGSExtractError, match="bwa-mem2 alignment is estimated"):
        _align(
            reads, tmp_path / "out", "--aligner", "bwa-mem2",
            "--native-aligners", "off",
        )  # fmt: skip


@pytest.mark.usefixtures("apple_silicon")
def test_native_aligners_off_still_needs_bwa_mem2s_0123_file(tmp_path, mem3_bin):
    # #658 review: upstream bwa-mem2 reads the .0123 file, so with the
    # installed wgse-bwa-mem2 0.5.0 turned off align builds it first.
    reads = _mem3_reads(tmp_path)
    Path(reads.ref + ".0123").unlink()
    _install_wgse_bwa_mem2(mem3_bin, "0.5.0")

    bam, result = _align(
        reads, tmp_path / "out", "--aligner", "bwa-mem2", "--native-aligners", "off"
    )

    assert _mapper(bam, "bwa-mem2").startswith("bwa-mem2 mem ")
    assert "native_aligner" not in result.metrics
    assert Path(reads.ref + ".0123").is_file()


# #672: on Linux, Bioconda's bwa-mem3 (its own Pixi environment, #591) stands
# in for bwa and bwa-mem2 with --compat, as wgse-bwa-mem3 does on Apple Silicon.
@pytest.fixture(params=["x86_64", "aarch64"])
def linux(request, monkeypatch, ram):
    """A Linux computer (x86-64 or ARM64) with 64 GiB of RAM."""
    monkeypatch.setattr(
        native_aligners,
        "platform",
        Namespace(system=lambda: "Linux", machine=lambda: request.param),
    )
    ram(64 * GIB)


def _env_bwa_mem3(fake_pixi, mem3_bin, source=None):
    """Bioconda's bwa-mem3, faked, in the installed bwa-mem3 environment."""
    bindir = fake_pixi.env("bwa-mem3", locked=("bwa-mem3",)) / "bin"
    if source is None:
        return _install_bwa_mem3(bindir, name="bwa-mem3", real_bin=mem3_bin)
    return python_tool(bindir, "bwa-mem3", source)


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize(
    ("aligner", "compat"), [("bwa", "bwa-mem"), ("bwa-mem2", "bwa-mem2")]
)
def test_linux_bwa_mem3_from_its_environment_stands_in_with_the_same_records(
    tmp_path, mem3_bin, fake_pixi, aligner, compat
):
    reads = _mem3_reads(tmp_path)
    _env_bwa_mem3(fake_pixi, mem3_bin)

    bam, result = _align(reads, tmp_path / "auto", "--aligner", aligner)
    upstream_bam, upstream = _align(
        reads, tmp_path / "off", "--aligner", aligner, "--native-aligners", "off"
    )

    # contract: --compat=<target> is what makes bwa-mem3 write the target's records.
    assert _mapper(bam, aligner).startswith(f"bwa-mem3 mem --compat={compat} ")
    assert _mapper(upstream_bam, aligner).startswith(f"{aligner} mem ")
    assert _samtools("view", bam) == _samtools("view", upstream_bam)
    assert result.metrics["aligner"] == aligner
    assert result.metrics["bwa_mem3_mode"] == f"compat={compat}"
    assert _native(result) == {
        "native_aligner": "bwa-mem3",
        "native_aligner_version": "0.16.0",
        "native_aligner_backend": "cpu",
    }
    assert result.provenance.tools["bwa-mem3"] == "0.16.0"
    assert "bwa_mem3_mode" not in upstream.metrics
    assert not result.warnings


@pytest.mark.usefixtures("linux_x86_64")
@pytest.mark.parametrize(
    ("cuda", "mapper", "backend"),
    [
        (True, "wgse-bwa --backend auto mem ", "cuda"),
        (False, "bwa-mem3 mem --compat=bwa-mem ", "cpu"),
    ],
)
def test_linux_prefers_wgse_bwa_on_cuda_then_bwa_mem3(
    tmp_path, mem3_bin, fake_pixi, cuda, mapper, backend
):
    # #674/#672: an NVIDIA GPU beats bwa-mem3 on the CPU; without one,
    # bwa-mem3 (85% less time than bwa on x86) beats wgse-bwa's CPU backend.
    reads = _mem3_reads(tmp_path)
    _install_build(mem3_bin, "bwa", _cuda_capabilities("bwa", cuda_available=cuda))
    _env_bwa_mem3(fake_pixi, mem3_bin)

    bam, result = _align(reads, tmp_path / "auto", "--aligner", "bwa")
    upstream_bam, _ = _align(
        reads, tmp_path / "off", "--aligner", "bwa", "--native-aligners", "off"
    )

    assert _mapper(bam).startswith(mapper)
    assert _samtools("view", bam) == _samtools("view", upstream_bam)
    assert result.metrics["aligner"] == "bwa"
    assert result.metrics["native_aligner_backend"] == backend
    assert not result.warnings


_CRASHING_BWA_MEM3 = """\
import os, signal

os.kill(os.getpid(), signal.SIGSEGV)
"""


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize(
    ("source", "problem"),
    [
        # Bioconda's x86-64 build crashed at startup on glibc 2.44 (#672).
        (_CRASHING_BWA_MEM3, "crashed (SIGSEGV)"),
        ("print('0.15.0')\n", "its version '0.15.0' isn't 0.16.0 or later"),
    ],
)
def test_linux_bwa_mem3_that_fails_its_check_warns_and_bwa_aligns(
    tmp_path, mem3_bin, fake_pixi, source, problem
):
    reads = _mem3_reads(tmp_path)
    _env_bwa_mem3(fake_pixi, mem3_bin, source)

    bam, result = _align(reads, tmp_path / "out", "--aligner", "bwa")

    assert _mapper(bam).startswith("bwa mem ")
    (warning,) = result.warnings
    assert warning.code == "native_aligner_unusable"
    assert problem in warning.message
    assert warning.details["aligner"] == "bwa"


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("command", ["align", "realign"])
def test_auto_aligns_with_wgse_bwa_mem2_0_5_0_without_upstream_or_0123(
    tmp_path, mem3_bin, caplog, command
):
    # #660: auto took bwa-mem2 only with upstream bwa-mem2 on PATH and the
    # .0123 file, neither of which wgse-bwa-mem2 0.5.0 needs. #664 review:
    # realign's preflight still reported the .0123 file missing.
    if command == "align":
        reads = _mem3_reads(tmp_path)
        ref = reads.ref
    else:
        source, ref = _mem3_realign_source(tmp_path)
    Path(ref + ".0123").unlink()
    (mem3_bin / "bwa-mem2").unlink()
    _install_wgse_bwa_mem2(mem3_bin, "0.5.0")
    outdir = tmp_path / "out"
    caplog.set_level("INFO")

    if command == "align":
        bam, result = _align(reads, outdir)
    else:
        result = _run_realign(source, ref, outdir)
        bam = str(outdir / "x_aligned.bam")

    assert _mapper(bam, "bwa").startswith("wgse-bwa-mem2 mem ")
    assert result.metrics["native_aligner"] == "wgse-bwa-mem2"
    assert not Path(ref + ".0123").exists()
    assert "Missing reference indexes" not in caplog.text


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize(
    ("version", "options", "mapper"),
    [
        ("0.5.0", (), "wgse-bwa-mem2 mem "),
        ("0.4.1", (), "wgse-bwa "),
        ("0.5.0", ("--native-aligners", "off"), "bwa mem "),
    ],
)
def test_auto_budgets_wgse_bwa_mem2_0_5_0_without_its_0123_file(
    tmp_path, mem3_bin, ram, version, options, mapper
):
    # #660: auto counted the .0123 file that wgse-bwa-mem2 0.5.0 doesn't load,
    # and picked bwa when only that build fit. Builds that load it, and
    # upstream bwa-mem2, still count it.
    reads = _mem3_reads(tmp_path)
    _bloat_0123(reads.ref)
    _install_wgse_bwa_mem2(mem3_bin, version)
    _install_build(mem3_bin, "bwa", _capabilities("bwa", metal_available=False))
    without_0123 = _mem3_needed(reads, "wgse-bwa-mem2", memory="256M")
    ram(64 * GIB, available=without_0123 + 2 * GIB)

    bam, _ = _align(reads, tmp_path / "out", *options)

    assert _mapper(bam, "bwa").startswith(mapper)


@pytest.mark.usefixtures("apple_silicon")
def test_auto_keeps_wgse_bwa_when_wgse_bwa_mem2_does_not_fit(tmp_path, mem3_bin, ram):
    # #660: a 16 GB Mac whose available RAM can't hold even wgse-bwa-mem2
    # 0.5.0's pipeline still aligns with wgse-bwa.
    reads = _mem3_reads(tmp_path)
    _install_wgse_bwa_mem2(mem3_bin, "0.5.0")
    _install_build(mem3_bin, "bwa", _capabilities("bwa", metal_available=False))
    ram(16 * GIB, available=_mem3_needed(reads, "wgse-bwa-mem2", memory="256M") // 2)

    bam, result = _align(reads, tmp_path / "out")

    assert _mapper(bam, "bwa").startswith("wgse-bwa ")
    assert result.metrics["native_aligner"] == "wgse-bwa"


@pytest.mark.usefixtures("apple_silicon")
def test_auto_keeps_wgse_bwa_mem2_when_only_it_fits_the_ram_available(
    tmp_path, mem3_bin, ram
):
    # #664 review: auto chose bwa-mem2 because wgse-bwa-mem2 fit the RAM
    # available, then the bigger wgse-bwa-mem3 stood in, checked only against
    # this Mac's RAM less headroom, and started over budget.
    reads = _mem3_reads(tmp_path)
    Path(reads.ref + ".0123").unlink()
    _install_bwa_mem3(mem3_bin, mem2="bwa")
    _install_wgse_bwa_mem2(mem3_bin, "0.5.0")
    mem2 = _mem3_needed(reads, "wgse-bwa-mem2", memory="256M")
    stand_in = _mem3_needed(reads, "bwa-mem3", memory="256M", gpu_bytes=GIB // 4)
    ram(64 * GIB, available=(mem2 + stand_in) // 2)

    bam, result = _align(reads, tmp_path / "out")

    assert _mapper(bam, "bwa").startswith("wgse-bwa-mem2 mem ")
    assert "bwa_mem3_mode" not in result.metrics


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("upstream", [True, False])
def test_auto_names_the_aligner_it_uses_for_an_unusable_wgse_bwa_mem2(
    tmp_path, mem3_bin, upstream
):
    # #664 review: auto's look at an unusable wgse-bwa-mem2 warned "Aligning
    # with bwa-mem2 instead" when, without upstream bwa-mem2, bwa aligned.
    reads = _mem3_reads(tmp_path)
    if not upstream:
        (mem3_bin / "bwa-mem2").unlink()
    _install_build(mem3_bin, "bwa-mem2", status=1)

    bam, result = _align(reads, tmp_path / "out")

    if upstream:
        assert _mapper(bam, "bwa-mem2").startswith("bwa-mem2 mem ")
        (warning,) = result.warnings
        assert warning.details["aligner"] == "bwa-mem2"
    else:
        assert _mapper(bam).startswith("bwa mem ")
        assert not result.warnings


@pytest.mark.usefixtures("apple_silicon")
def test_auto_with_only_wgse_bwa_mem3_keeps_bwas_records(tmp_path, mem3_bin):
    # #660 owner rule: wgse-bwa-mem3 alone (no .0123 file, no upstream
    # bwa-mem2) stands in for bwa with bwa's records; auto doesn't switch to
    # bwa-mem2's.
    reads = _mem3_reads(tmp_path)
    Path(reads.ref + ".0123").unlink()
    (mem3_bin / "bwa-mem2").unlink()
    _install_bwa_mem3(mem3_bin, mem2="bwa")

    _, result = _align(reads, tmp_path / "out")

    assert result.metrics["aligner"] == "bwa"
    assert result.metrics["bwa_mem3_mode"] == "compat=bwa-mem"
