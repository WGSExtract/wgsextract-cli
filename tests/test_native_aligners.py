"""``align`` and ``realign`` with WGS Extract's Apple Silicon aligner builds
(wgse-bwa, wgse-bwa-mem3, wgse-minimap2) on PATH, and with bwa-mem3.

Most tests fake the builds at their boundary: a ``wgse-bwa`` that answers
``capabilities --json`` and otherwise runs the real bwa under its own name,
as the real build does (its ``@PG`` CL names ``wgse-bwa`` and its backend
option). The fake bwa-mem3 runs the real bwa or bwa-mem2 whose records its
``--compat`` mode writes. Apple Silicon, its GPU and the Mac's RAM are the
mocked platform. The real-Metal #747 case instead uses the installed build
and mocks only psutil; it skips when that hardware/build is unavailable.

Shared fixtures and helpers remain here; focused GPU-memory, mem3, Linux and
minimap2 cases live in the sibling test_native_aligners_* modules.
"""

import json
import logging
from argparse import Namespace
from pathlib import Path

import pytest

from tests.fake_tools import only_tools, python_tool
from tests.htslib_helpers import run_wgsextract, write_fasta
from tests.prepared_data import sparse_truncate
from tests.test_alignment_commands import _programs, _samtools, _short_reads
from tests.test_realign import _args as _realign_args
from tests.test_realign import _old_alignment
from wgsextract_cli.commands import realign
from wgsextract_cli.core import native_aligners, results
from wgsextract_cli.core.alignment.memory import (
    bwa_gpu_memory,
    estimate_mapping_memory,
)
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.utils import UsageError

GIB = 1024**3

_FAKE_BUILD = """\
import os, sys

args = sys.argv[1:]
if args[:1] == ["capabilities"]:
    sys.stdout.write({capabilities!r})
    sys.exit({status})
backend = args[:2] if args[:1] == ["--backend"] else []
if backend == ["--backend", "auto"] and {auto_diagnostic!r}:
    print({auto_diagnostic!r}, file=sys.stderr, flush=True)
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


def _install_build(
    bindir, engine, capabilities=None, status=0, real=None, *, auto_diagnostic=""
):
    """A wgse-<engine> in bindir running the real ``real`` (else ``engine``)."""
    name = f"wgse-{engine}"
    source = _FAKE_BUILD.format(
        capabilities=_capabilities(engine) if capabilities is None else capabilities,
        status=status,
        real=str((bindir / (real or engine)).resolve()),
        name=name,
        auto_diagnostic=auto_diagnostic,
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
    memory_options = ["--memory", memory] if memory is not None else []
    result = _run(
        "align", "--r1", reads.r1, "--r2", reads.r2, "--ref", reads.ref,
        "--outdir", str(out), "--threads", "2", *memory_options, *options,
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


@pytest.mark.usefixtures("apple_silicon")
def test_metal_watchdog_start_failure_preserves_cpu_alignment_1228(
    tmp_path, bwa_bin, caplog
):
    reads = _short_reads(tmp_path)
    reason = "could not start the Metal watchdog thread"
    _install_build(
        bwa_bin,
        "bwa",
        auto_diagnostic=f"[wgse-bwa] CPU fallback: GPU watchdog: {reason}",
    )

    with caplog.at_level(logging.DEBUG):
        fallback_bam, result = _align(reads, tmp_path / "fallback")
    cpu_bam, _ = _align(reads, tmp_path / "cpu", "--native-aligners", "off")

    assert _samtools("view", fallback_bam) == _samtools("view", cpu_bam)
    # The manifest names the selected backend; the log reports runtime fallback.
    assert result.metrics["native_aligner_backend"] == "metal"
    assert reason in caplog.text


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


# A comment per read that makes _short_reads' files fill 2 threads' 1G sorts.
_GENOME_SIZED = {"comment": 100_000}


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


def _mem3_reads(tmp_path, **options):
    """Reads whose reference has bwa's index and bwa-mem2's, which bwa-mem3
    maps with."""
    reads = _short_reads(tmp_path, **options)
    for indexer in (["bwa", "index"], ["bwa-mem2", "index"]):
        _run_tool([*indexer, reads.ref])
    return reads


def _run_tool(argv):
    from wgsextract_cli.core.utils import run_command

    run_command(argv, capture_output=True)


def _bloat_0123(ref):
    """Make bwa-mem2's .0123 file a sparse 64 GiB (so bwa-mem2 can't load it)."""
    sparse_truncate(Path(ref + ".0123"), 64 * GIB)


def _mem3_needed(reads, engine, available_reads=2 * 140, **options):
    """The pipeline's memory estimate with ``engine`` (as _align runs it)."""
    return estimate_mapping_memory(
        reads.ref, engine, threads="2", samblaster=False, reads=available_reads,
        read_length=100, **{"memory": "1G", **options},
    ).total  # fmt: skip


def _mem3_realign_source(tmp_path):
    genome = _short_reads(tmp_path / "reads").genome[:6_000]
    (tmp_path / "old").mkdir()
    ref = str(write_fasta(tmp_path / "ref.fa", {"chr1": genome}))
    for indexer in (["bwa", "index"], ["bwa-mem2", "index"]):
        _run_tool([*indexer, ref])
    source, _ = _old_alignment(tmp_path, genome)
    return source, ref


def _install_wgse_bwa_mem2(bindir, version):
    """wgse-bwa-mem2 ``version``, writing bwa's records with bwa: the test's
    .0123 file is missing or too big for upstream bwa-mem2 to load."""
    capabilities = _capabilities("bwa-mem2", version=version, metal_available=False)
    _install_build(bindir, "bwa-mem2", capabilities, real="bwa")


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


_CRASHING_BWA_MEM3 = """\
import os, signal

os.kill(os.getpid(), signal.SIGSEGV)
"""


def _linux_wgse_bwa_mem3_capabilities(**overrides):
    """The Linux x86-64 wgse-bwa-mem3 package's capabilities (#674)."""
    document = {
        "version": "0.4.0",
        "metal_available": False,
        "metal_hardware_supported": False,
        "cuda_available": True,
        "cuda_hardware_supported": True,
        **overrides,
    }
    return _capabilities("bwa-mem3", **document)


@pytest.fixture
def glibc_2_44(monkeypatch):
    """The C library of the host where Bioconda's bwa-mem3 crashed (#727)."""
    monkeypatch.setattr(
        native_aligners.os,
        "confstr",
        lambda name: "glibc 2.44" if name == "CS_GNU_LIBC_VERSION" else None,
        raising=False,  # Windows' os has no confstr.
    )


# #674: wgse-minimap2 0.4.0 on Linux x86-64 runs long reads' gap fills on an
# NVIDIA GPU with --backend auto; sr, older builds and ARM64 stay on the CPU.
def _align_single(reads, out, *options):
    result = _run(
        "align", "--r1", reads.r1, "--ref", reads.ref, "--outdir", str(out),
        "--threads", "2", "--memory", "1G", "--aligner", "minimap2", *options,
    )  # fmt: skip
    (bam,) = out.glob("*_aligned.bam")
    return str(bam), result


def _minimap2_cuda(version="0.4.0", cuda_available=True):
    """The Linux x86-64 wgse-minimap2 package's capabilities on a CUDA machine
    (cuda_available is false while its GPU's memory is taken)."""
    return _capabilities(
        "minimap2",
        version=version,
        metal_available=False,
        metal_hardware_supported=False,
        cuda_available=cuda_available,
        cuda_hardware_supported=True,
    )


def _linux(monkeypatch, machine):
    monkeypatch.setattr(
        native_aligners,
        "platform",
        Namespace(system=lambda: "Linux", machine=lambda: machine),
    )
