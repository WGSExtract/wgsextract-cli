"""Focused native aligners linux regressions."""

from argparse import Namespace
from pathlib import Path

import pytest

from tests.test_alignment_commands import _samtools
from tests.test_native_aligners import (
    _CRASHING_BWA_MEM3,
    _GENOME_SIZED,
    GIB,
    _align,
    _bloat_0123,
    _capabilities,
    _cuda_capabilities,
    _env_bwa_mem3,
    _install_build,
    _install_bwa_mem3,
    _install_wgse_bwa_mem2,
    _linux_wgse_bwa_mem3_capabilities,
    _mapper,
    _mem3_needed,
    _mem3_reads,
    _mem3_realign_source,
    _native,
    _run_realign,
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
from wgsextract_cli.core import native_aligners
from wgsextract_cli.core.utils import WGSExtractError


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
@pytest.mark.parametrize("cuda", [True, False])
@pytest.mark.parametrize("options", [("--aligner", "bwa"), ()])
def test_736_linux_prefers_bwa_mem3_to_wgse_bwa_on_cuda(
    tmp_path, mem3_bin, fake_pixi, cuda, options
):
    # #736: on an RTX 3090, bwa-mem3 --compat=bwa-mem on the CPU (98 s) beat
    # wgse-bwa --cuda (131 s) with the same records, yet the GPU went first.
    reads = _mem3_reads(tmp_path)
    # Without bwa-mem2's .0123 file auto keeps bwa's records.
    Path(reads.ref + ".0123").unlink(missing_ok=True)
    _install_build(mem3_bin, "bwa", _cuda_capabilities("bwa", cuda_available=cuda))
    _env_bwa_mem3(fake_pixi, mem3_bin)

    bam, result = _align(reads, tmp_path / "auto", *options)
    upstream_bam, _ = _align(
        reads, tmp_path / "off", "--aligner", "bwa", "--native-aligners", "off"
    )

    # contract: --compat=bwa-mem is what makes bwa-mem3 write bwa's records.
    assert _mapper(bam).startswith("bwa-mem3 mem --compat=bwa-mem ")
    assert _samtools("view", bam) == _samtools("view", upstream_bam)
    assert result.metrics["aligner"] == "bwa"
    assert result.metrics["native_aligner"] == "bwa-mem3"
    assert result.metrics["native_aligner_backend"] == "cpu"
    assert not result.warnings


@pytest.mark.usefixtures("linux_x86_64")
@pytest.mark.parametrize("why", ["crashes", "does not fit"])
def test_736_wgse_bwa_on_cuda_aligns_when_bwa_mem3_cannot(
    tmp_path, mem3_bin, fake_pixi, ram, why
):
    # #736 keeps wgse-bwa --cuda next, for a bwa-mem3 that crashes (#727) or
    # whose pipeline doesn't fit the RAM available.
    reads = _mem3_reads(tmp_path, **_GENOME_SIZED)
    _install_build(mem3_bin, "bwa", _cuda_capabilities("bwa"))
    _env_bwa_mem3(fake_pixi, mem3_bin, _CRASHING_BWA_MEM3 if why == "crashes" else None)
    if why == "does not fit":
        bwa = _mem3_needed(reads, "bwa")
        stand_in = _mem3_needed(reads, "bwa-mem3")
        assert stand_in > bwa  # so halfway leaves room for bwa only
        ram(64 * GIB, available=(bwa + stand_in) // 2)

    bam, result = _align(reads, tmp_path / "out", "--aligner", "bwa")
    upstream_bam, _ = _align(
        reads, tmp_path / "off", "--aligner", "bwa", "--native-aligners", "off"
    )

    assert _mapper(bam).startswith("wgse-bwa --backend auto mem ")
    assert _samtools("view", bam) == _samtools("view", upstream_bam)
    assert result.metrics["native_aligner"] == "wgse-bwa"
    assert result.metrics["native_aligner_backend"] == "cuda"
    if why == "crashes":
        (warning,) = result.warnings
        assert warning.code == "native_aligner_unusable"


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


@pytest.mark.parametrize(
    ("machine", "mapper", "native"),
    [
        (
            "x86_64",
            "wgse-bwa-mem3 --backend auto mem --compat=bwa-mem2 ",
            ("wgse-bwa-mem3", "0.4.0", "cuda"),
        ),
        # wgse-bwa-mem3 is built for x86-64 only.
        ("aarch64", "bwa-mem3 mem --compat=bwa-mem2 ", ("bwa-mem3", "0.16.0", "cpu")),
    ],
)
def test_linux_x86_64_wgse_bwa_mem3_stands_in_before_biocondas_on_its_gpu(
    tmp_path, mem3_bin, fake_pixi, monkeypatch, ram, machine, mapper, native
):
    # #674: the Linux package, found on PATH as wgse-bwa does, runs its CUDA
    # backend in Bioconda's bwa-mem3's place, with the same records.
    monkeypatch.setattr(
        native_aligners,
        "platform",
        Namespace(system=lambda: "Linux", machine=lambda: machine),
    )
    ram(64 * GIB)
    reads = _mem3_reads(tmp_path)
    _install_bwa_mem3(mem3_bin, capabilities=_linux_wgse_bwa_mem3_capabilities())
    _env_bwa_mem3(fake_pixi, mem3_bin)

    bam, result = _align(reads, tmp_path / "auto", "--aligner", "bwa-mem2")
    upstream_bam, _ = _align(
        reads, tmp_path / "off", "--aligner", "bwa-mem2", "--native-aligners", "off"
    )

    assert _mapper(bam, "bwa-mem2").startswith(mapper)
    assert _samtools("view", bam) == _samtools("view", upstream_bam)
    assert _native(result) == dict(
        zip(
            ("native_aligner", "native_aligner_version", "native_aligner_backend"),
            native,
            strict=True,
        )
    )
    assert not result.warnings


@pytest.mark.usefixtures("linux_x86_64")
@pytest.mark.parametrize("options", [("--aligner", "bwa"), ()])
def test_linux_wgse_bwa_mem3_goes_ahead_of_wgse_bwa_on_cuda(
    tmp_path, mem3_bin, fake_pixi, options
):
    # #742 review: with both packages installed, bwa's records come from
    # wgse-bwa-mem3 (#736's order), on its own GPU.
    reads = _mem3_reads(tmp_path)
    # Without bwa-mem2's .0123 file auto keeps bwa's records.
    Path(reads.ref + ".0123").unlink(missing_ok=True)
    _install_build(mem3_bin, "bwa", _cuda_capabilities("bwa"))
    _install_bwa_mem3(mem3_bin, capabilities=_linux_wgse_bwa_mem3_capabilities())
    _env_bwa_mem3(fake_pixi, mem3_bin)

    bam, result = _align(reads, tmp_path / "auto", *options)
    upstream_bam, _ = _align(
        reads, tmp_path / "off", "--aligner", "bwa", "--native-aligners", "off"
    )

    # contract: --compat=bwa-mem is what makes bwa-mem3 write bwa's records.
    assert _mapper(bam).startswith("wgse-bwa-mem3 --backend auto mem --compat=bwa-mem ")
    assert _samtools("view", bam) == _samtools("view", upstream_bam)
    assert result.metrics["aligner"] == "bwa"
    assert result.metrics["native_aligner"] == "wgse-bwa-mem3"
    assert result.metrics["native_aligner_backend"] == "cuda"
    assert not result.warnings


@pytest.mark.usefixtures("linux_x86_64")
@pytest.mark.parametrize(
    ("bioconda", "mapper", "aligner"),
    [
        (None, "bwa-mem3 mem --compat=bwa-mem ", "bwa-mem3"),
        ("print('0.15.0')\n", "bwa mem ", "bwa"),
    ],
)
def test_linux_unusable_wgse_bwa_mem3_warns_naming_what_aligns_instead(
    tmp_path, mem3_bin, fake_pixi, bioconda, mapper, aligner
):
    # #742 review: with Bioconda's bwa-mem3 unusable too, bwa aligns, and the
    # warning must say so rather than name bwa-mem3.
    reads = _mem3_reads(tmp_path)
    build = _install_bwa_mem3(
        mem3_bin, capabilities=_linux_wgse_bwa_mem3_capabilities(cpu_available=False)
    )
    _env_bwa_mem3(fake_pixi, mem3_bin, bioconda)

    bam, result = _align(reads, tmp_path / "out", "--aligner", "bwa")

    assert _mapper(bam).startswith(mapper)
    warnings = {w.details["binary"]: w for w in result.warnings}
    assert warnings["wgse-bwa-mem3"].code == "native_aligner_unusable"
    assert warnings["wgse-bwa-mem3"].details == {
        "binary": "wgse-bwa-mem3",
        "path": str(build),
        "aligner": aligner,
    }


@pytest.mark.usefixtures("linux_x86_64", "glibc_2_44")
def test_727_crashing_bwa_mem3_stand_in_falls_back_and_names_the_cause(
    tmp_path, mem3_bin, fake_pixi
):
    # #727: Bioconda's bwa-mem3 0.16.0 segfaults at startup on glibc 2.44;
    # standing in for bwa it must not take the alignment down with it.
    reads = _mem3_reads(tmp_path)
    _env_bwa_mem3(fake_pixi, mem3_bin, _CRASHING_BWA_MEM3)

    bam, result = _align(reads, tmp_path / "out")

    # Auto's next choice for these reads, bwa-mem2, aligns.
    assert _mapper(bam, "bwa-mem2").startswith("bwa-mem2 mem ")
    assert result.metrics["aligner"] == "bwa-mem2"
    (warning,) = result.warnings
    assert warning.code == "native_aligner_unusable"
    assert warning.details["aligner"] == "bwa-mem2"
    assert "SIGSEGV" in warning.message
    assert "glibc 2.44" in warning.message and "#727" in warning.message


@pytest.mark.usefixtures("linux_x86_64", "glibc_2_44")
@pytest.mark.parametrize(
    ("command", "options"),
    [
        ("align", ("--aligner", "bwa-mem3")),
        ("align", ("--fast",)),
        ("realign", ("--aligner", "bwa-mem3")),
    ],
)
def test_727_explicit_bwa_mem3_that_crashes_stops_with_the_cause(
    tmp_path, mem3_bin, fake_pixi, command, options
):
    # #727: --aligner bwa-mem3 ran the crashing build and failed with a bare
    # "exit -11" and errors from samblaster and samtools sort downstream.
    _env_bwa_mem3(fake_pixi, mem3_bin, _CRASHING_BWA_MEM3)
    out = tmp_path / "out"
    if command == "align":
        reads = _mem3_reads(tmp_path)
        run = lambda: _align(reads, out, *options)  # noqa: E731
    else:
        source, ref = _mem3_realign_source(tmp_path)
        run = lambda: _run_realign(source, ref, out, aligner="bwa-mem3")  # noqa: E731

    with pytest.raises(WGSExtractError) as raised:
        run()

    message = str(raised.value)
    assert f"{' '.join(options)} needs bwa-mem3" in message
    assert "crashed (SIGSEGV)" in message and "glibc 2.44" in message
    assert not list(out.glob("*_aligned.*"))


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
    Path(reads.ref + ".0123").unlink(missing_ok=True)
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
    Path(reads.ref + ".0123").unlink(missing_ok=True)
    (mem3_bin / "bwa-mem2").unlink()
    _install_bwa_mem3(mem3_bin, mem2="bwa")

    _, result = _align(reads, tmp_path / "out")

    assert result.metrics["aligner"] == "bwa"
    assert result.metrics["bwa_mem3_mode"] == "compat=bwa-mem"
