"""wgse-bwa's optional dense suffix-array file (#543): `ref dense-sa` builds,
lists and deletes it, and `align`/`realign` have wgse-bwa read it only when
asked (--dense-suffix-array or config dense_suffix_array).

wgse-bwa is faked at its boundary as in test_native_aligners: ``capabilities
--json [index]`` answers as the real build (a side file counts when it starts
with the real file's magic), ``densify-sa`` writes such a file, and ``mem``
runs the real bwa under the build's name and options, so the BAM's @PG line
shows the options it was given. Apple Silicon and its RAM are mocked.
"""

import shutil
from pathlib import Path

import pytest

from tests.htslib_helpers import write_fasta
from tests.test_alignment_commands import _samtools, _short_reads
from tests.test_native_aligners import (  # noqa: F401 (fixtures)
    _align,
    _capabilities,
    _env_bwa_mem3,
    _install_bwa_mem3,
    _mapper,
    _mem3_reads,
    _run,
    _run_realign,
    apple_silicon,
    linux_x86_64,
)
from tests.test_realign import _old_alignment
from wgsextract_cli.core import dense_suffix_array
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.resource_estimates import estimate_resources
from wgsextract_cli.core.utils import (
    MissingDependencyError,
    UsageError,
    WGSExtractError,
    run_command,
)

MAGIC = b"WGSEDSA1"

_FAKE_WGSE_BWA = """\
import glob, json, os, re, sys

args = sys.argv[1:]
if args[:1] == ["capabilities"]:
    document = json.loads({capabilities!r})
    if len(args) > 2:
        found = sorted(
            int(m[1])
            for p in glob.glob(glob.escape(args[2]) + ".wgse-sa*")
            if (m := re.fullmatch(r".*\\.wgse-sa(\\d+)", p))
        )
        good = [i for i in found if open(f"{{args[2]}}.wgse-sa{{i}}", "rb").read(8) == {magic!r}]
        document["dense_sa"] = {{"intervals": found, "interval": good[0] if good else None}}
    print(json.dumps(document))
    sys.exit(0)
if args[:1] == ["densify-sa"]:
    interval, ref = args[args.index("-i") + 1], args[-1]
    with open(f"{{ref}}.wgse-sa{{interval}}", "wb") as out:
        out.write({magic!r} + bytes(4096))
    sys.exit(0)
options = []
while args and args[0] in ("--backend", "--dense-sa"):
    options += args[:2]
    args = args[2:]
os.execv({real!r}, [" ".join(["wgse-bwa", *options]), *args])
"""


@pytest.fixture
def bwa_bin(request):
    from tests.fake_tools import only_tools

    return only_tools(request, "bwa", "samtools")


@pytest.fixture
def mem3_bin(request):
    from tests.fake_tools import only_tools

    return only_tools(request, "bwa", "bwa-mem2", "samtools")


def _install(bindir, version="0.11.0", **capabilities):
    from tests.fake_tools import python_tool

    source = _FAKE_WGSE_BWA.format(
        capabilities=_capabilities("bwa", version=version, **capabilities),
        magic=MAGIC,
        real=str((bindir / "bwa").resolve()),
    )
    return python_tool(bindir, "wgse-bwa", source)


def _index(ref):
    run_command(["bwa", "index", ref], capture_output=True)


def _side_file(ref, interval=16, content=MAGIC + bytes(4096)):
    path = Path(f"{ref}.wgse-sa{interval}")
    path.write_bytes(content)
    return path


def _dense_warnings(result):
    return [w for w in result.warnings if w.code == "dense_suffix_array_unused"]


@pytest.mark.usefixtures("apple_silicon")
def test_align_never_reads_a_present_file_unless_asked(tmp_path, bwa_bin):
    reads = _short_reads(tmp_path)
    _install(bwa_bin)
    _index(reads.ref)
    _side_file(reads.ref)

    bam, result = _align(reads, tmp_path / "out")

    # contract: wgse-bwa 0.11 reads any present file by default; the owner's
    # decision (#543) is opt-in only, so the run must switch it off.
    assert _mapper(bam).startswith("wgse-bwa --backend auto --dense-sa off mem ")
    assert "dense_suffix_array_interval" not in result.metrics
    assert not result.warnings


@pytest.mark.parametrize("machine", ["apple_silicon", "linux_x86_64"])
@pytest.mark.parametrize("ask", ["flag", "config"])
def test_asked_wgse_bwa_reads_the_file_with_the_same_records(
    tmp_path, bwa_bin, monkeypatch, ask, machine, request
):
    request.getfixturevalue(machine)
    reads = _short_reads(tmp_path)
    _install(
        bwa_bin,
        metal_available=machine == "apple_silicon",
        cuda_available=machine == "linux_x86_64",
    )
    _index(reads.ref)
    _side_file(reads.ref)
    upstream, _ = _align(reads, tmp_path / "off", "--native-aligners", "off")
    options = ["--dense-suffix-array"] if ask == "flag" else []
    if ask == "config":
        monkeypatch.setitem(settings, "dense_suffix_array", True)

    bam, result = _align(reads, tmp_path / "dense", *options)

    assert _mapper(bam).startswith("wgse-bwa --backend auto --dense-sa on mem ")
    assert result.metrics["dense_suffix_array_interval"] == 16
    assert _samtools("view", bam) == _samtools("view", upstream)
    assert not result.warnings


@pytest.mark.usefixtures("linux_x86_64")
@pytest.mark.parametrize(
    "setup", ["cuda", "cpu", "dense exceeds budget", "cpu fallback"]
)
def test_680_709_cuda_first_routing_keeps_dense_sa_only_for_wgse_bwa(
    tmp_path, mem3_bin, fake_pixi, ram, setup
):
    reads = _mem3_reads(tmp_path)
    _install(mem3_bin, metal_available=False, cuda_available=setup != "cpu")
    if setup != "cpu fallback":
        _env_bwa_mem3(fake_pixi, mem3_bin)
    side = _side_file(reads.ref)
    if setup in {"dense exceeds budget", "cpu fallback"}:
        # A sparse file makes the requested index exceed the GPU budget
        # without allocating its contents; bwa-mem3 doesn't load this file.
        with side.open("r+b") as handle:
            handle.truncate(8 * 1024**3)
        ram(8 * 1024**3)

    bam, result = _align(
        reads,
        tmp_path / "out",
        "--aligner",
        "bwa",
        "--dense-suffix-array",
        memory="256M",
    )

    if setup == "cuda":
        # contract: auto retains CPU fallback; on is required to read the file.
        assert _mapper(bam).startswith("wgse-bwa --backend auto --dense-sa on mem ")
        assert result.metrics["native_aligner_backend"] == "cuda"
        assert result.metrics["dense_suffix_array_interval"] == 16
        assert not _dense_warnings(result)
    elif setup == "cpu fallback":
        assert _mapper(bam).startswith("wgse-bwa --dense-sa on mem ")
        assert result.metrics["native_aligner_backend"] == "cpu"
        assert result.metrics["dense_suffix_array_interval"] == 16
        assert not _dense_warnings(result)
    else:
        assert _mapper(bam).startswith("bwa-mem3 mem --compat=bwa-mem ")
        (warning,) = _dense_warnings(result)
        assert warning.details == {"reason": "other_aligner", "aligner": "bwa-mem3"}
        assert "dense_suffix_array_interval" not in result.metrics


@pytest.mark.usefixtures("apple_silicon")
def test_the_run_flag_overrides_the_config(tmp_path, bwa_bin, monkeypatch):
    reads = _short_reads(tmp_path)
    _install(bwa_bin)
    _index(reads.ref)
    _side_file(reads.ref)
    monkeypatch.setitem(settings, "dense_suffix_array", True)

    bam, result = _align(reads, tmp_path / "out", "--no-dense-suffix-array")

    assert "--dense-sa off mem " in _mapper(bam)
    assert not result.warnings


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("no file", "missing"),
        ("damaged file", "unusable"),
        ("old wgse-bwa", "wgse_bwa_too_old"),
        ("native off", "no_wgse_bwa"),
    ],
)
def test_asked_but_unusable_warns_and_aligns_without_it(
    tmp_path, bwa_bin, setup, reason
):
    reads = _short_reads(tmp_path)
    _install(bwa_bin, version="0.10.0" if setup == "old wgse-bwa" else "0.11.0")
    _index(reads.ref)
    if setup == "damaged file":
        _side_file(reads.ref, content=b"truncated")
    options = ["--native-aligners", "off"] if setup == "native off" else []

    bam, result = _align(reads, tmp_path / "out", "--dense-suffix-array", *options)

    (warning,) = _dense_warnings(result)
    assert warning.details["reason"] == reason
    assert "dense_suffix_array_interval" not in result.metrics
    assert "--dense-sa on" not in _mapper(bam)


@pytest.mark.usefixtures("apple_silicon")
def test_asked_while_wgse_bwa_mem3_stands_in_warns_and_keeps_the_stand_in(
    tmp_path, mem3_bin
):
    # #709 review: the stand-in dropped a requested file with only a log line.
    reads = _mem3_reads(tmp_path)
    _install(mem3_bin)
    _install_bwa_mem3(mem3_bin)
    _side_file(reads.ref)

    bam, result = _align(
        reads, tmp_path / "out", "--aligner", "bwa", "--dense-suffix-array"
    )

    assert _mapper(bam).startswith("wgse-bwa-mem3 ")
    (warning,) = _dense_warnings(result)
    assert warning.details == {"reason": "other_aligner", "aligner": "wgse-bwa-mem3"}
    assert "dense_suffix_array_interval" not in result.metrics


@pytest.mark.usefixtures("apple_silicon")
def test_asked_when_auto_picks_bwa_mem2_warns(tmp_path, mem3_bin):
    reads = _mem3_reads(tmp_path)
    _install(mem3_bin)
    _side_file(reads.ref)

    bam, result = _align(reads, tmp_path / "out", "--dense-suffix-array")

    assert result.metrics["aligner"] == "bwa-mem2"
    (warning,) = _dense_warnings(result)
    assert warning.details == {"reason": "other_aligner", "aligner": "bwa-mem2"}


@pytest.mark.usefixtures("apple_silicon")
def test_realign_streams_into_wgse_bwa_reading_the_file_when_asked(tmp_path, bwa_bin):
    genome = _short_reads(tmp_path / "reads").genome[:6_000]
    (tmp_path / "old").mkdir()
    ref = str(write_fasta(tmp_path / "ref.fa", {"chr1": genome}))
    source, _ = _old_alignment(tmp_path, genome)
    _install(bwa_bin)
    _index(ref)
    _side_file(ref)

    result = _run_realign(source, ref, tmp_path / "out", dense_suffix_array=True)

    assert "--dense-sa on mem " in _mapper(str(tmp_path / "out" / "x_aligned.bam"))
    assert result.metrics["dense_suffix_array_interval"] == 16
    assert not _dense_warnings(result)


def test_the_estimate_counts_the_file_as_index_memory(tmp_path):
    reads = _short_reads(tmp_path)

    def estimate(**extra):
        return estimate_resources(
            "align",
            input_paths=[reads.r1, reads.r2],
            output_dir=str(tmp_path),
            reference=reads.ref,
            threads="2",
            memory="1G",
            reads=280,
            **extra,
        )

    def memory(plan):
        (ram,) = [r for r in plan.resources if r.resource == "memory"]
        return ram.estimated_bytes

    without, with_file = estimate(), estimate(dense_sa_bytes=800_000_000)

    assert memory(with_file) - memory(without) == 800_000_000
    assert any("dense suffix-array" in note for note in with_file.notes)


def _ref_dense_sa(*options):
    return _run("ref", "dense-sa", *options)


def test_ref_dense_sa_builds_lists_and_deletes_the_file(tmp_path, bwa_bin):
    reads = _short_reads(tmp_path)
    _install(bwa_bin)
    _index(reads.ref)

    built = _ref_dense_sa("--ref", reads.ref, "--interval", "8")
    listed = _ref_dense_sa("--ref", reads.ref, "--list")
    deleted = _ref_dense_sa("--ref", reads.ref, "--delete")

    target = f"{reads.ref}.wgse-sa8"
    assert [o.path for o in built.outputs] == [target]
    assert built.metrics["dense_suffix_array_interval"] == 8
    assert built.provenance.tools["wgse-bwa"] == "0.11.0"
    (row,) = listed.details["dense_suffix_array_files"]
    assert (row["path"], row["interval"]) == (target, 8)
    assert "wgse-bwa reads this one" in listed.text
    assert "Deleted 1" in deleted.text
    assert not list(Path(reads.ref).parent.glob("*.wgse-sa*"))  # manifests too


def test_ref_dense_sa_needs_room_beside_the_file_it_replaces(
    tmp_path, bwa_bin, monkeypatch
):
    # #709 review: densify-sa writes a .partial before replacing the old file,
    # so the old file's space can't count as free.
    reads = _short_reads(tmp_path)
    _install(bwa_bin)
    _index(reads.ref)
    old = _side_file(reads.ref, content=MAGIC + bytes(1 << 20))
    planned = dense_suffix_array.planned_bytes(reads.ref, 16)
    free = planned + (1 << 30) - 1
    monkeypatch.setattr(
        shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(free * 4, 0, free)
    )

    with pytest.raises(WGSExtractError, match="of disk"):
        _ref_dense_sa("--ref", reads.ref)

    assert old.read_bytes()[:8] == MAGIC and old.stat().st_size == 8 + (1 << 20)


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        ("no index", "no complete BWA index"),
        ("no wgse-bwa", "isn't installed"),
        ("old wgse-bwa", "predates dense suffix-array files"),
    ],
)
def test_ref_dense_sa_refuses_what_it_cannot_build_from(
    tmp_path, bwa_bin, setup, error
):
    reads = _short_reads(tmp_path)
    if setup != "no wgse-bwa":
        _install(bwa_bin, version="0.10.0" if setup == "old wgse-bwa" else "0.11.0")
    if setup != "no index":
        _index(reads.ref)

    with pytest.raises((UsageError, MissingDependencyError), match=error):
        _ref_dense_sa("--ref", reads.ref)

    assert not list(Path(reads.ref).parent.glob("*.wgse-sa*"))


def test_ref_dense_sa_lists_the_cost_before_a_file_is_built(tmp_path, bwa_bin):
    reads = _short_reads(tmp_path)
    _index(reads.ref)

    listed = _ref_dense_sa("--ref", reads.ref, "--list")

    assert listed.details["dense_suffix_array_files"] == []
    assert "builds one (0.0 MB)" in listed.text
