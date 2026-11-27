"""Focused deepvariant staging regressions."""

import base64
import gzip
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.disk_volumes import GIB, Volumes
from tests.htslib_helpers import (
    needs_tools,
    run_wgsextract,
    sam_to_bam,
    vcf_calls,
    write_fasta,
)
from tests.test_deepvariant import (
    apple as apple,
)
from tests.test_deepvariant import (
    arm64 as arm64,
)
from tests.test_deepvariant import (
    as_cram,
    call,
    dv_flags,
    read_names,
    rerun,
    visible,
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
from wgsextract_cli.core.alignment.cram_staging import stage_cram30
from wgsextract_cli.core.utils import (
    WGSExtractError,
    run_command,
)


@needs_tools("samtools")
def test_283_staging_keeps_tp_arrays_that_the_old_decoder_corrupts(
    tmp_path: Path,
) -> None:
    # The pinned DeepVariant Ultima image reports "invalid element_size for
    # tag tp" on read1 of this valid CRAM 3.1's SSE4 rANS-Nx16 block.
    fixture = Path(__file__).with_name("fixtures") / "ultima-tp-rans.cram.b64"
    source = tmp_path / "synthetic.cram"
    source.write_bytes(base64.b64decode(fixture.read_bytes()))
    run_command(["samtools", "index", source])
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "ACGT" * 250})
    work = tmp_path / "stage"
    work.mkdir()
    staged = Path(
        stage_cram30(
            str(source), str(ref), str(work), "1", "chr1:1-1000",
            tool="DeepVariant", why="the old SSE4 rANS decoder corrupts tp",
        )
    )  # fmt: skip
    assert staged.read_bytes()[:6] == b"CRAM\x03\x00"
    expected_tags = [
        "tp:B:C,"
        + ",".join(
            ["0"] * 4097
            + [str(v) for v in bytes.fromhex("00020200020000000002020100020100")]
        ),
        "tp:B:C,17",
    ]
    for reads in (source, staged):
        output = run_command(
            ["samtools", "view", "-T", ref, reads, "chr1:1-1000"], capture_output=True
        )
        assert [
            tag
            for line in output.stdout.splitlines()
            for tag in line.split("\t")[11:]
            if tag.startswith("tp:")
        ] == expected_tags


def test_691_calling_profile_reaches_deepvariant_without_a_compatibility_copy(dv):
    from wgsextract_cli.commands.align import run_streamed_alignment

    output = dv.tmp / "calling.cram"
    run_streamed_alignment(
        ["samtools", "view", "-h", dv.sample.bam],
        str(output),
        threads="1",
        memory="64M",
        fmt="CRAM",
        ref=dv.sample.ref,
        duplicates=None,
        output_profile="calling",
    )
    call(dv, reads=str(output))
    (examples,) = dv.deepvariant.runs("make_examples")
    assert Path(examples["reads"]).resolve() == output.resolve()
    assert examples["cram_version"] == [3, 0]
    assert vcf_calls(dv.out / "deepvariant.vcf.gz") == dv.sample.calls
    assert not (dv.out / deepvariant.WORK_DIRECTORY).exists()


@pytest.mark.parametrize("reads", ["bam", "3.0", "3.1", "3.1 named .bam"])
def test_only_cram_31_is_staged_as_indexed_cram_30(dv, caplog, reads):
    """The images' htslib can't decode CRAM 3.1 (the fake fails on it), so
    those reads, whatever their name, are copied to CRAM 3.0 first."""
    sample = dv.sample
    if reads == "bam":
        alignment = sample.bam
    else:
        name = "reads.bam" if reads.endswith(".bam") else None
        alignment = as_cram(sample, reads.split()[0], name)
    before = Path(alignment).read_bytes()
    with caplog.at_level("INFO"):
        call(dv, reads=alignment)

    (examples,) = dv.deepvariant.runs("make_examples")
    staged = reads.startswith("3.1")
    assert examples["cram_version"] == (None if reads == "bam" else [3, 0])
    assert (Path(examples["reads"]).name == "reads.cram") == staged
    assert examples["names"] == read_names(sample.bam, sample.ref)
    assert caplog.text.count("Transcoding CRAM") == staged
    assert vcf_calls(dv.out / "deepvariant.vcf.gz") == sample.calls
    assert not (dv.out / deepvariant.WORK_DIRECTORY).exists()
    assert Path(alignment).read_bytes() == before


@pytest.mark.parametrize(
    ("region", "expected"),
    [
        (None, []),
        ("chr1:900-1000", ["chr1:900-1000"]),
        ("chr1", []),
        ("chr1:600-700 chr1:2000-2100", ["chr1:600-700", "chr1:2000-2100"]),
        ("chr1:900-1000 chr1:900-950", ["chr1:900-1000"]),
        ("bed", ["chr1:900-1000"]),
        ("bed.gz", ["chr1:900-1000"]),
        ("bed-mixed", ["chr1:900-1000", "chr1:2000-2100"]),
        ("bedpe", ["chr1:600-2100"]),  # One span from both ends.
    ],
)
def test_staging_keeps_the_reads_of_the_selected_regions(dv, region, expected):
    cram = as_cram(dv.sample, "3.1")
    if region and region.startswith("bed"):
        selection = dv.tmp / ("selection." + region.split("-")[0])
        rows = (
            "chr1\t599\t700\tchr1\t1999\t2100\n"
            if region == "bedpe"
            else "chr1\t899\t1000\nchr1\t899\t950\n"
        )
        if region == "bed.gz":
            with gzip.open(selection, "wt") as stream:
                stream.write(rows)
        else:
            selection.write_text(rows)
        region = str(selection) + (" chr1:2000-2100" if region == "bed-mixed" else "")
    call(dv, *(["-r", region] if region else []), reads=cram)
    (examples,) = dv.deepvariant.runs("make_examples")
    assert examples["cram_version"] == [3, 0]
    assert examples["names"] == read_names(cram, dv.sample.ref, *expected)


@pytest.mark.parametrize(
    ("selection", "reads"),
    [
        ("HLA-A*01:01:01:01", range(49)),
        ("HLA-A*01:01:01:01:1,001-2,000", range(10, 20)),
        ("HLA-A*01:01:01:01:1,001", [10]),
    ],
)
def test_colon_contigs_select_like_deepvariant_does(dv, selection, reads):
    """A contig name with colons is a whole contig; a range after it is
    read like DeepVariant (Nucleus) reads it."""
    contig = "HLA-A*01:01:01:01"
    (dv.tmp / "hla").mkdir()
    ref = write_fasta(dv.tmp / "hla" / "hla.fa", {contig: "ACGT" * 1250})
    lines = ["@HD\tVN:1.6\tSO:unsorted", f"@SQ\tSN:{contig}\tLN:5000"]
    for i in range(49):  # r{i} covers 100i+1 to 100i+50.
        lines.append(
            f"r{i}\t0\t{contig}\t{100 * i + 1}\t60\t50M\t*\t0\t0\t"
            f"{'ACGT' * 12}AC\t{'I' * 50}"
        )
    hla = SimpleNamespace(
        ref=str(ref), bam=sam_to_bam(lines, dv.tmp / "hla" / "hla.bam")
    )
    cram = as_cram(hla, "3.1")
    run_wgsextract(
        "vcf", "deepvariant", "--input", cram, "--ref", str(ref),
        "--outdir", str(dv.out), "-r", selection,
    )  # fmt: skip
    (examples,) = dv.deepvariant.runs("make_examples")
    assert examples["names"] == sorted(f"r{i}" for i in reads)


def test_unknown_contigs_are_dropped_from_a_selection_but_not_alone(dv):
    cram = as_cram(dv.sample, "3.1")
    bed = dv.tmp / "capture.bed"
    bed.write_text("chrUn_stray\t0\t50\nchr1\t899\t1000\nchr1\t949\t1100\n")
    call(dv, "-r", f"{bed} chrUn_stray:1-10", reads=cram)
    (examples,) = dv.deepvariant.runs("make_examples")
    assert examples["names"] == read_names(cram, dv.sample.ref, "chr1:900-1100")

    bed.write_text("chrUn_stray\t0\t50\n")
    dv.docker.clear()
    for region, message in [
        (str(bed), "covers no reference bases"),
        ("chrUn_stray:1-10", "covers no reference bases"),
        ("chr1 chrUn_stray", "Invalid interval"),
    ]:
        with pytest.raises(WGSExtractError, match=message):
            call(dv, "-r", region, reads=cram, out=dv.tmp / "refused")
    assert dv.docker.runs() == []


def test_a_relative_region_bed_in_its_own_folder_reaches_the_image(dv, monkeypatch):
    """Regression: a BED beside neither the reads, the reference nor the
    output wasn't mounted, so DeepVariant's image couldn't open it."""
    (dv.tmp / "targets").mkdir()
    (dv.tmp / "targets" / "panel.bed").write_text("chr1\t899\t1100\n")
    monkeypatch.chdir(dv.tmp / "targets")
    call(dv, "-r", "panel.bed")
    assert vcf_calls(dv.out / "deepvariant.vcf.gz") == dv.sample.calls[:1]
    assert dv_flags(dv)["regions"] == str(dv.tmp / "targets" / "panel.bed")


def test_missing_region_bed_is_reported_before_docker_runs(dv):
    with pytest.raises(WGSExtractError, match="Invalid DeepVariant region selection"):
        call(dv, "-r", str(dv.tmp / "missing.bed"))
    assert dv.docker.runs() == []


def test_staging_needs_disk_for_the_whole_input_or_the_selected_slices(dv, big_cram):
    big_cram.volumes.sizes[Path(big_cram.path)] = 16 * GIB
    # #690: the requirement counts the CRAM 3.0 copy before anything runs.
    with pytest.raises(WGSExtractError, match=r"requirement: it needs ~\d+ GiB disk"):
        call(dv, reads=big_cram.path)
    assert not list(dv.out.rglob("reads.cram"))
    assert dv.deepvariant.runs("make_examples") == []

    # A region needs only its CRAM slices, and at least 64 MiB.
    call(dv, "-r", "chr1:900-1000", reads=big_cram.path)
    assert dv.deepvariant.runs("make_examples")
    big_cram.volumes.free[dv.out] = 63 / 1024
    # Staging checks its slices itself, past --no-preflight's estimate.
    with pytest.raises(WGSExtractError, match=r"~0\.06 GiB"):
        call(dv, "-r", "chr1:900-1100", "--no-preflight", reads=big_cram.path,
             out=dv.out / "again")  # fmt: skip


@pytest.mark.parametrize("compressed", [False, True])
def test_capture_staging_counts_the_selected_slices_not_the_genome_fraction(
    dv, big_cram, compressed
):
    data = f"0\t1\t100\t100\t0\t{16 * GIB}\n".encode()
    Path(f"{big_cram.path}.crai").write_bytes(
        gzip.compress(data) if compressed else data
    )
    # Staging's own check, past --no-preflight's requirement (#690).
    with pytest.raises(WGSExtractError, match=r"~32\.00 GiB"):
        call(dv, "-r", "chr1:10-20", "--no-preflight", reads=big_cram.path)


@pytest.mark.parametrize(
    "index_data",
    [
        b"",
        b"malformed\n",
        b"0 1 20 100 0 50\nbad trailing row\n",
        b"9 1 20 100 0 50\n",
        gzip.compress(b"0 1 20 100 0 50\n", mtime=0)[:-4],
        b"\x1f\x8b\x08\x00" + bytes(6) + b"\x07",
    ],
)
def test_an_unreadable_crai_bounds_staging_by_the_whole_input(dv, big_cram, index_data):
    """Each of these CRAIs would say 50 bytes (so 64 MiB) were selected."""
    # A missing CRAI is refused before staging (index_missing, #1182).
    Path(f"{big_cram.path}.crai").write_bytes(index_data)
    big_cram.volumes.free[dv.out] = 1.5
    with pytest.raises(WGSExtractError, match=r"~2\.00 GiB"):
        call(dv, "-r", "chr1:10", "--no-preflight", reads=big_cram.path)


def test_crai_slices_follow_the_header_order_and_shared_slices_count_once(
    dv, monkeypatch
):
    """Slices of 1 GiB units: chrM and chr20 share the first one."""
    work = dv.tmp / "two"
    work.mkdir()
    seqs = {"chrM": "ACGT" * 250, "chr20": "TGCA" * 250}
    ref = write_fasta(work / "ref.fa", seqs)
    lines = ["@HD\tVN:1.6\tSO:coordinate", "@SQ\tSN:chrM\tLN:1000"]
    lines += ["@SQ\tSN:chr20\tLN:1000", "r1\t0\tchrM\t1\t60\t4M\t*\t0\t0\tACGT\tIIII"]
    sample = SimpleNamespace(ref=str(ref), bam=sam_to_bam(lines, work / "two.bam"))
    cram = as_cram(sample, "3.1")
    dv.out.mkdir()
    volumes = Volumes(monkeypatch)
    volumes.free[dv.out] = 350
    Path(f"{cram}.crai").write_text(
        f"0\t1\t100\t100\t10\t{200 * GIB}\n"
        f"1\t1\t100\t100\t10\t{200 * GIB}\n"
        f"1\t101\t20\t500\t10\t{300 * GIB}\n"
        f"1\t121\t20\t900\t10\t{400 * GIB}\n"
    )
    for region, needs in [
        ("chr20:11-20 chrM:11-20", "400.00"),
        ("chr20:101-120", "600.00"),
    ]:
        with pytest.raises(WGSExtractError, match=rf"needs ~{needs} GiB"):
            run_wgsextract(
                "vcf", "deepvariant", "--input", cram, "--ref", str(ref),
                "--outdir", str(dv.out), "-r", region, "--no-preflight",
            )  # fmt: skip


@pytest.mark.parametrize("failure", [None, "view", "index", "caller", "cancel"])
def test_staged_reads_are_kept_for_a_rerun_and_removed_after_success(
    dv, caplog, failure
):
    cram = as_cram(dv.sample, "3.1")
    before = Path(cram).read_bytes()
    staged = dv.out / deepvariant.WORK_DIRECTORY / "reads" / "reads.cram"
    if failure in ("view", "index"):
        # A folder where samtools writes makes it fail.
        blocker = staged if failure == "view" else Path(f"{staged}.crai")
        blocker.mkdir(parents=True)
    elif failure == "caller":
        dv.deepvariant.fail("make_examples")
    elif failure == "cancel":
        dv.deepvariant.fail("call_variants", interrupt=os.getpid())
    if failure:
        with pytest.raises(
            KeyboardInterrupt if failure == "cancel" else WGSExtractError
        ):
            call(dv, reads=cram)
        assert visible(dv.out) == []
        assert staged.parent.is_dir()  # Kept for the rerun.
        if failure in ("view", "index"):
            blocker.rmdir()
    caplog.clear()
    with caplog.at_level("INFO"):
        rerun(dv, reads=cram)
    # A rerun stages the reads again only if staging didn't finish.
    assert ("Transcoding CRAM" in caplog.text) == (failure in (None, "view", "index"))
    if failure == "cancel":
        assert dv.deepvariant.stages() == ["call_variants", "postprocess_variants"]
    else:
        (examples,) = dv.deepvariant.runs("make_examples")
        assert examples["reads"] == str(staged)
    assert not staged.parent.parent.exists()
    assert Path(cram).read_bytes() == before
    assert visible(dv.out) == ["deepvariant.vcf.gz", "deepvariant.vcf.gz.tbi"]
