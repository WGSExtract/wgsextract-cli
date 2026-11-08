"""``align`` and ``realign`` with WGS Extract's Apple Silicon aligner builds
(wgse-bwa, wgse-minimap2) on PATH.

The builds are faked at their boundary: a ``wgse-bwa`` that answers
``capabilities --json`` and otherwise runs the real bwa under its own name,
as the real build does (its ``@PG`` CL names ``wgse-bwa`` and its backend
option). Apple Silicon, its GPU and the Mac's RAM are the mocked platform.
"""

import json
from argparse import Namespace

import pytest

from tests.fake_tools import only_tools, python_tool
from tests.htslib_helpers import run_wgsextract, write_fasta
from tests.test_alignment_commands import _programs, _samtools, _short_reads
from tests.test_realign import _args as _realign_args
from tests.test_realign import _old_alignment
from wgsextract_cli.commands import realign
from wgsextract_cli.core import native_aligners, results
from wgsextract_cli.core.alignment_memory import (
    bwa_metal_memory,
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
os.execv({real!r}, [" ".join([{name!r}, *backend]), *args[len(backend):]])
"""


def _capabilities(tool, **overrides):
    document = {
        "schema_version": 1,
        "version": {"bwa": "0.7.1", "minimap2": "0.1.0"}[tool],
        "engine": tool,
        "engine_version": {"bwa": "0.7.19", "minimap2": "2.31-r1302"}[tool],
        "qualified": False,
        "metal_available": tool == "bwa",
        "metal_hardware_supported": tool == "bwa",
        "cpu_available": True,
    }
    document.update(overrides)
    return json.dumps(document) + "\n"


def _install_build(bindir, engine, capabilities=None, status=0):
    """A wgse-<engine> in bindir running the real ``engine``."""
    name = f"wgse-{engine}"
    source = _FAKE_BUILD.format(
        capabilities=_capabilities(engine) if capabilities is None else capabilities,
        status=status,
        real=str((bindir / engine).resolve()),
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
def bwa_bin(request):
    return only_tools(request, "bwa", "samtools")


def _run(*argv):
    """Run a command line; its result (metrics, warnings, tools)."""
    result = results.CommandResult(command=argv[0])
    with results.collecting(result):
        run_wgsextract(*argv)
    result.finalize()
    return result


def _align(reads, out, *options):
    result = _run(
        "align", "--r1", reads.r1, "--r2", reads.r2, "--ref", reads.ref,
        "--outdir", str(out), "--threads", "2", "--memory", "1G", *options,
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


def _run_realign(source, ref, outdir):
    result = results.CommandResult(command="realign")
    with results.collecting(result):
        realign.run(_realign_args(source, ref=ref, outdir=str(outdir)))
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


@pytest.mark.parametrize(
    "machine", [("Darwin", "x86_64"), ("Linux", "aarch64"), ("Linux", "x86_64")]
)
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
        gpu_bytes=bwa_metal_memory(reads.ref, 2 * 140),
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

    bam, result = _align(reads, tmp_path / "out", "--memory", "256M")

    assert _mapper(bam).startswith("wgse-bwa mem -K 100000000 ")
    assert _sort_memory(bam) == "256M"
    assert result.metrics["native_aligner_backend"] == "cpu"


@pytest.mark.usefixtures("apple_silicon")
def test_a_genome_too_big_for_the_macs_ram_still_aligns_on_metal(
    tmp_path, bwa_bin, ram
):
    # #549: a 30x genome on a 16 GB M1 was estimated over RAM with or without
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
    ram(16 * GIB, available=upstream + bwa_metal_memory(ref, reads) // 2)
    outdir = tmp_path / "out"

    result = _run_realign(source, ref, outdir)

    bam = str(outdir / "x_aligned.bam")
    assert _mapper(bam).startswith("wgse-bwa mem ") and " -p " in _mapper(bam)
    steps = json.loads((outdir / "x_aligned.bam.steps.json").read_text())["steps"]
    assert "extract" not in steps
    assert not list(outdir.glob("*.fastq.gz"))
    assert result.metrics["native_aligner_backend"] == "cpu"
