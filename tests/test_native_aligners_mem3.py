"""Focused native aligners mem3 regressions."""

import json
from argparse import Namespace
from pathlib import Path

import pytest

from tests.htslib_helpers import write_fasta
from tests.test_alignment_commands import _samtools, _short_reads
from tests.test_native_aligners import (
    _GENOME_SIZED,
    GIB,
    _align,
    _bloat_0123,
    _capabilities,
    _install_build,
    _install_bwa_mem3,
    _install_wgse_bwa_mem2,
    _mapper,
    _mem3_needed,
    _mem3_reads,
    _mem3_realign_source,
    _native,
    _run_realign,
    _run_tool,
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
from wgsextract_cli.core import native_aligners
from wgsextract_cli.core.alignment.memory import (
    estimate_mapping_memory,
)
from wgsextract_cli.core.utils import UsageError, WGSExtractError


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
    reads = _mem3_reads(tmp_path, **_GENOME_SIZED)
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
    [(("Linux", "aarch64"), ()), (("Darwin", "arm64"), ("--native-aligners", "off"))],
)
def test_fast_aligns_with_bwa_mem3s_preset_and_warns(
    tmp_path, mem3_bin, monkeypatch, ram, machine, options
):
    # Where wgse-bwa-mem3 isn't built (Linux ARM64), or with native aligners
    # off, bwa-mem3 from PATH runs even with wgse-bwa-mem3 installed.
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
    assert "needs wgse-bwa-mem3" in message
    assert "or bwa-mem3 (Bioconda" in message
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


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("aligner", ["bwa", "auto"])
def test_bwa_aligns_when_only_its_pipeline_fits_the_ram_available(
    tmp_path, mem3_bin, ram, aligner
):
    # #590 review: the stand-in for bwa was checked against total RAM, so with
    # too little available it took bwa's place (and auto, having dropped
    # bwa-mem2 for low available RAM, picked the bigger stand-in). A sparse
    # .0123 makes bwa-mem2's index hs38-sized next to bwa-mem3's.
    reads = _mem3_reads(tmp_path, **_GENOME_SIZED)
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
    Path(reads.ref + ".0123").unlink(missing_ok=True)
    _install_wgse_bwa_mem2(mem3_bin, "0.5.0")

    bam, result = _align(
        reads, tmp_path / "out", "--aligner", "bwa-mem2", "--native-aligners", "off"
    )

    assert _mapper(bam, "bwa-mem2").startswith("bwa-mem2 mem ")
    assert "native_aligner" not in result.metrics
    assert Path(reads.ref + ".0123").is_file()
