"""`vcf freebayes` and `vcf gatk` (CPU HaplotypeCaller) on a tiny genome."""

import ctypes
import gzip
import json
import logging
import os
import platform
import re
import shutil
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

import pytest

from tests.fake_tools import FakeGatk, require_bare_name_fakes
from tests.htslib_helpers import (
    needs_tools,
    require_runnable,
    run_wgsextract,
    snp_sample,
    soft_mask_reference,
    vcf_body,
    vcf_calls,
    write_fasta,
)
from wgsextract_cli.commands.vcf import callers
from wgsextract_cli.core import paths
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.utils import UsageError, run_command
from wgsextract_cli.main import main

SNP = 1000  # 1-based position of the sample's homozygous SNP
SNP2 = 1550  # and of a second one, so two regions each hold a call


@pytest.fixture(scope="module")
def genome(tmp_path_factory) -> Namespace:
    """A sample homozygous for SNPs at SNP and SNP2, as a BAM and a CRAM."""
    require_runnable("samtools")
    sample = snp_sample(tmp_path_factory.mktemp("callers"), snps=(SNP, SNP2))
    sample.call = sample.calls[0]
    return sample


def _call(caller: str, genome: Namespace, input_path: str, outdir: Path, *extra: str):
    require_runnable(caller, "samtools", "tabix")
    run_wgsextract("vcf", caller, "--input", input_path, "--ref", genome.ref,
                   "--outdir", str(outdir), *extra)  # fmt: skip
    vcf = outdir / f"{caller}.vcf.gz"
    assert Path(f"{vcf}.tbi").is_file()
    return [call[:5] for call in vcf_calls(vcf)]


# --- freebayes -------------------------------------------------------------------

needs_freebayes = needs_tools("freebayes", "samtools", "bcftools", "tabix")


@needs_freebayes
def test_freebayes_calls_the_snp_from_a_bam(genome, tmp_path):
    assert _call("freebayes", genome, genome.bam, tmp_path) == genome.calls


@needs_freebayes
def test_freebayes_streams_a_cram_through_samtools(genome, tmp_path):
    assert _call("freebayes", genome, genome.cram, tmp_path) == genome.calls


@needs_freebayes
def test_freebayes_decompresses_a_gzipped_reference_and_removes_it(genome, tmp_path):
    require_runnable("freebayes", "samtools", "tabix")
    gz = tmp_path / "ref" / "ref.fa.gz"
    gz.parent.mkdir()
    with open(gz, "wb") as f:
        run_command(["bgzip", "-c", genome.ref], stdout=f)
    run_command(["samtools", "faidx", str(gz)])
    outdir = tmp_path / "out"
    run_wgsextract("vcf", "freebayes", "--input", genome.bam, "--ref", str(gz),
                   "--outdir", str(outdir))  # fmt: skip
    assert vcf_calls(outdir / "freebayes.vcf.gz") == genome.calls
    assert not list(outdir.glob(".wgse_ref_*"))
    assert not (gz.parent / "ref.fa").exists()


@needs_freebayes
def test_freebayes_with_nothing_in_the_region_warns(genome, tmp_path, caplog):
    assert (
        _call("freebayes", genome, genome.bam, tmp_path, "-r", "chr1:2000-2100") == []
    )
    assert "No variants were called in chr1:2000-2100" in caplog.text


@needs_freebayes
def test_freebayes_bam_region_includes_its_first_base(genome, tmp_path):
    region = f"chr1:{SNP}-{SNP + 50}"
    assert _call("freebayes", genome, genome.bam, tmp_path, "-r", region) == [
        genome.call
    ]


@needs_freebayes
def test_freebayes_cram_region_excludes_calls_outside_it(genome, tmp_path):
    region = f"chr1:{SNP + 10}-{SNP + 50}"
    assert _call("freebayes", genome, genome.cram, tmp_path, "-r", region) == []


@needs_freebayes
def test_1247_freebayes_region_bed_keeps_grouped_coordinates_and_colons(
    genome, tmp_path
):
    bed = tmp_path / "regions.bed"
    callers.write_region_bed(
        "chr1:1,000-1,050, HLA-A*01:01:2-10",
        {"chr1": 5000, "HLA-A*01:01": 100},
        str(bed),
    )
    assert bed.read_text(encoding="utf-8").splitlines() == [
        "chr1\t999\t1050",
        "HLA-A*01:01\t1\t10",
    ]
    assert _call(
        "freebayes", genome, genome.cram, tmp_path, "-r", "chr1:1,000-1,050"
    ) == [genome.call]
    callers.write_region_bed(
        "chr1:1,000,chr2:2,000-,chr3:-3,000",
        {"chr1": 5000, "chr2": 5000, "chr3": 5000},
        str(bed),
    )
    assert bed.read_text(encoding="utf-8").splitlines() == [
        "chr1\t999\t5000",
        "chr2\t1999\t5000",
        "chr3\t0\t3000",
    ]
    with pytest.raises(UsageError, match="Ambiguous --region"):
        callers.write_region_bed(
            "chr1:100-200", {"chr1": 5000, "chr1:100-200": 101}, str(bed)
        )


# --- GATK HaplotypeCaller --------------------------------------------------------


@pytest.mark.parametrize("caller", ["freebayes", "gatk"])
def test_1654_native_callers_preserve_records_on_soft_masked_reference(
    caller, genome, tmp_path
):
    require_runnable(caller, "samtools", "bcftools", "bgzip", "tabix")
    masked = soft_mask_reference(genome.ref, tmp_path / "masked.fa", 900, 1100)
    bodies = []
    for label, ref in [("plain", genome.ref), ("masked", masked)]:
        out = tmp_path / label
        run_wgsextract(
            "vcf", caller, "--input", genome.bam, "--ref", ref,
            "--outdir", str(out), "--threads", "1", "-r", "chr1:900-1100",
        )  # fmt: skip
        vcf = out / f"{caller}.vcf.gz"
        assert vcf_calls(vcf, "chr1:900-1100") == [genome.call]
        bodies.append(vcf_body(vcf))
    assert bodies[0] == bodies[1]


@needs_tools("gatk", "samtools", "tabix")
def test_gatk_calls_the_snp_from_a_regional_cram(genome, tmp_path):
    """One real GATK run (slow to start): the CRAM is cut to a temporary BAM,
    the missing .dict is made beside the reference, and nothing else is left."""
    require_runnable("gatk", "samtools", "tabix")
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    ref = ref_dir / "ref.fa"
    ref.write_text(Path(genome.ref).read_text())
    run_command(["samtools", "faidx", str(ref)])
    outdir = tmp_path / "out"
    run_wgsextract(
        "vcf", "gatk", "--input", Path(genome.cram).as_posix(),
        "--ref", ref.as_posix(), "--outdir", outdir.as_posix(),
        "-r", "chr1:900-1100",
    )  # fmt: skip

    assert [c[:5] for c in vcf_calls(outdir / "gatk.vcf.gz")] == [genome.call]
    assert (ref_dir / "ref.dict").read_text().startswith("@HD")
    assert sorted(p.name for p in outdir.iterdir()) == [
        "gatk.vcf.gz",
        "gatk.vcf.gz.tbi",
    ]


def _calling_cram(genome, cram):
    run_command(
        [
            "samtools", "view", "-C", "-T", genome.ref,
            "--output-fmt-option", "version=3.0", "-o", cram, genome.bam,
        ]
    )  # fmt: skip
    run_command(["samtools", "index", cram])


@needs_tools("gatk", "samtools", "bcftools", "tabix")
@pytest.mark.parametrize("gvcf", [False, True], ids=["vcf", "gvcf"])
def test_1125_gatk_whole_contig_cram_matches_staged_bam(genome, tmp_path, caplog, gvcf):
    if callers._gatk_version() != "4.6.2.0":
        pytest.skip("requires the qualified GATK version")
    cram = tmp_path / "calling.cram"
    _calling_cram(genome, cram)
    staged = tmp_path / "regional.bam"
    run_command(
        ["samtools", "view", "-bh", "-T", genome.ref, "-o", staged, cram, "chr1"]
    )
    run_command(["samtools", "index", staged])

    with caplog.at_level(logging.INFO):
        for mode, path in (("staged", staged), ("direct", cram)):
            run_wgsextract(
                "vcf", "gatk", "--input", str(path), "--ref", genome.ref,
                "--outdir", str(tmp_path / mode), "--threads", "1",
                "--region", "chr1",
                *(["--reference-evidence", "gvcf"] if gvcf else []),
            )  # fmt: skip
    assert "Reading indexed CRAM directly" in caplog.text
    for filename in ["gatk.vcf.gz", "gatk.g.vcf.gz"] if gvcf else ["gatk.vcf.gz"]:
        bodies = [
            run_command(
                ["bcftools", "view", "-H", tmp_path / mode / filename],
                capture_output=True,
            ).stdout
            for mode in ("staged", "direct")
        ]
        assert bodies[0] and bodies[0] == bodies[1]


@needs_tools("gatk", "samtools", "bcftools", "tabix")
@pytest.mark.parametrize("index_case", ["different-sidecars", "space-delimited"])
def test_1125_incompatible_crai_still_stages_the_whole_contig(
    genome, tmp_path, caplog, index_case
):
    cram = tmp_path / "calling.cram"
    _calling_cram(genome, cram)
    index = Path(f"{cram}.crai")
    if index_case == "different-sidecars":
        cram.with_suffix(".crai").write_bytes(index.read_bytes())
    else:
        with gzip.open(index, "rt") as source:
            text = source.read()
        with gzip.open(index, "wt") as target:
            target.write(text.replace("\t", " "))
    with caplog.at_level(logging.INFO):
        assert (
            _call("gatk", genome, str(cram), tmp_path / "out", "-r", "chr1")
            == genome.calls
        )
    assert "Preparing regional BAM" in caplog.text


def _1000_genomes_style_cram(genome: Namespace, folder: Path) -> Path:
    """The sample's CRAM 3.0 with 50 unmapped reads after the mapped ones, and
    a .crai whose unmapped-slice row has the read length (100) as its span, as
    the 1000 Genomes CRAMs' indexes do; samtools writes 0 or 1 there."""
    folder.mkdir()
    sam = run_command(["samtools", "view", "-h", genome.bam],
                      capture_output=True).stdout.splitlines()  # fmt: skip
    sam += [
        f"u{i}\t4\t*\t0\t0\t*\t*\t0\t0\t{'ACGT' * 25}\t{'I' * 100}" for i in range(50)
    ]
    (folder / "s.sam").write_text("\n".join(sam) + "\n")
    cram = folder / "s.cram"
    run_command(["samtools", "sort", "-O", "cram", "--reference", genome.ref,
                 "--output-fmt-option", "version=3.0", "-o", str(cram),
                 str(folder / "s.sam")])  # fmt: skip
    run_command(["samtools", "index", str(cram)])
    index = Path(f"{cram}.crai")
    with gzip.open(index, "rt") as f:
        rows = [line.split("\t") for line in f.read().splitlines()]
    assert [row[0] for row in rows][-1] == "-1"
    with gzip.open(index, "wt") as f:
        f.writelines(
            "\t".join(row[:2] + ["100"] + row[3:] if row[0] == "-1" else row) + "\n"
            for row in rows
        )
    return cram


@needs_tools("gatk", "samtools", "tabix")
@pytest.mark.parametrize(
    ("linked", "regional"),
    [(False, False), (True, False), (False, True)],
    ids=["whole", "linked-whole", "whole-contig"],
)
def test_1034_gatk_reads_a_cram_whose_crai_htsjdk_rejects(
    genome, tmp_path, caplog, linked, regional
):
    """#1034 (vcf gatk rejects 1000 Genomes CRAM indexes): htsjdk refused the
    .crai ("unmapped slice with unexpected alignment start (0) or span"). GATK
    now reads the whole CRAM through a fresh temporary index in --tmp-dir, and
    the CRAM and its index are left as they were. htsjdk also finds the index
    beside the file a link without one points to."""
    require_runnable("gatk", "samtools", "tabix")
    ref = _reference_copy(genome, tmp_path / "ref")
    cram = _1000_genomes_style_cram(genome, tmp_path / "reads")
    before = {p.name: p.read_bytes() for p in cram.parent.iterdir()}
    reads = tmp_path / "link.cram" if linked else cram
    if linked:
        try:
            reads.symlink_to(cram)
        except OSError:
            pytest.skip("can't make a symbolic link here")
    scratch, outdir = tmp_path / "scratch", tmp_path / "out"
    scratch.mkdir()
    caplog.set_level(logging.INFO)
    run_wgsextract(
        "vcf", "gatk", "--input", reads.as_posix(), "--ref", ref,
        "--outdir", outdir.as_posix(), "--tmp-dir", scratch.as_posix(),
        *(["--region", "chr1"] if regional else []),
    )  # fmt: skip

    assert [c[:5] for c in vcf_calls(outdir / "gatk.vcf.gz")] == genome.calls
    if regional:
        assert "Preparing regional BAM" in caplog.text
    else:
        assert "can't read the CRAM index" in caplog.text
        assert "span 100" in caplog.text
    assert {p.name: p.read_bytes() for p in cram.parent.iterdir()} == before
    assert list(scratch.iterdir()) == []


@needs_tools("gatk", "samtools")
def test_1125_whole_contig_still_requires_a_cram_index(tmp_path, genome):
    from wgsextract_cli.core.artifact_checks import ArtifactIncompatible

    require_runnable("gatk", "samtools")
    cram = tmp_path / "no-index.cram"
    shutil.copyfile(genome.cram, cram)
    with pytest.raises(ArtifactIncompatible, match="index .* is missing"):
        run_wgsextract(
            "vcf", "gatk", "--input", str(cram), "--ref", genome.ref,
            "--outdir", str(tmp_path / "out"), "--region", "chr1",
        )  # fmt: skip
    assert not Path(f"{cram}.crai").exists()


# --- GATK at its process boundary ----------------------------------------------
# GATK is slow to start and not in the default environment: FakeGatk calls with
# bcftools instead and checks what GATK itself would refuse.


@pytest.fixture
def gatk(tmp_path, monkeypatch) -> FakeGatk:
    require_bare_name_fakes()
    require_runnable("samtools", "bcftools", "tabix")
    bcftools = shutil.which("bcftools") or pytest.skip("bcftools is not on PATH")
    bindir = tmp_path / "bin"
    fake = FakeGatk(bindir, bcftools, version="4.7.0.0")
    # The OS temp folder, where GATK's temp dir goes without a --tmp-dir.
    (tmp_path / "systemp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "systemp"))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    # The fake runs anywhere; real GATK is refused on Linux ARM64 (#236).
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    clear_tool_caches()
    return fake


def _reference_copy(genome: Namespace, folder: Path, *, gzip: bool = False) -> str:
    folder.mkdir(parents=True)
    ref = folder / "ref.fa"
    shutil.copy(genome.ref, ref)
    if gzip:
        with open(f"{ref}.gz", "wb") as f:
            run_command(["bgzip", "-c", str(ref)], stdout=f)
        ref.unlink()
        ref = folder / "ref.fa.gz"
    run_command(["samtools", "faidx", str(ref)])
    return str(ref)


def _gatk(capsys, genome: Namespace, input_path: str, ref: str, outdir: Path,
          scratch: Path | None, *extra: str) -> tuple[dict, str]:  # fmt: skip
    """`vcf gatk` through the CLI: its --json result manifest and its log.
    Without a scratch folder the temporary BAM goes beside the output."""
    if scratch:
        scratch.mkdir(exist_ok=True)
        extra = ("--tmp-dir", str(scratch), *extra)
    try:
        main(["vcf", "gatk", "--input", input_path, "--ref", ref,
              "--outdir", str(outdir), *extra, "--json"])  # fmt: skip
    except SystemExit:
        pass
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


def test_gatk_cuts_a_regional_cram_to_a_bam_it_can_read(genome, gatk, tmp_path, capsys):
    """A GATK that can't read CRAM 3.1 still calls a CRAM region: the CRAM is cut
    to a temporary BAM of every requested interval (given out of order), and
    the missing .dict is made beside the reference. Nothing else is left beside
    the output or in the OS temp folder."""
    gatk.set(reads_cram=False)
    ref = _reference_copy(genome, tmp_path / "ref")
    outdir = tmp_path / "out"
    result, log = _gatk(capsys, genome, genome.cram, ref, outdir, None,
                   "-r", f"chr1:{SNP2 - 50}-{SNP2 + 50}, chr1:{SNP - 100}-{SNP + 100}")  # fmt: skip

    assert result["status"] == "ok", result["error"]
    assert vcf_calls(outdir / "gatk.vcf.gz") == genome.calls
    assert (tmp_path / "ref" / "ref.dict").read_text().startswith("@HD")
    assert sorted(p.name for p in outdir.iterdir()) == [
        "gatk.vcf.gz",
        "gatk.vcf.gz.tbi",
        "manifests",
    ]
    assert (outdir / "manifests" / "gatk.vcf.manifest.json").is_file()
    assert list((tmp_path / "systemp").iterdir()) == []


def test_gatk_reads_a_whole_cram_without_converting_it(genome, gatk, tmp_path, capsys):
    """Without a region there's no cut to a BAM; a GATK that reads CRAM gets it."""
    ref = _reference_copy(genome, tmp_path / "ref")
    outdir = tmp_path / "out"
    result, _ = _gatk(capsys, genome, genome.cram, ref, outdir, tmp_path / "scratch")

    assert result["status"] == "ok", result["error"]
    assert vcf_calls(outdir / "gatk.vcf.gz") == genome.calls
    # contract: the whole CRAM is read in place, not copied to a BAM first.
    assert [run[run.index("-I") + 1] for run in gatk.runs()] == [genome.cram]


def test_691_gatk_refuses_whole_archive_cram_with_a_compatible_output_hint(
    genome, gatk, tmp_path, capsys
):
    gatk.set(version="4.6.2.0")
    ref = _reference_copy(genome, tmp_path / "ref")
    cram = str(tmp_path / "archive.cram")
    run_command(["samtools", "view", "-C", "-T", ref,
                 "--output-fmt-option", "archive", "--output-fmt-option", "version=3.1",
                 "-o", cram, genome.bam])  # fmt: skip
    run_command(["samtools", "index", cram])
    result, _ = _gatk(capsys, genome, cram, ref, tmp_path / "out", tmp_path / "scratch")
    assert result["status"] == "failed"
    assert result["exit_code"] == 2
    assert "--output-profile calling" in result["error"]["message"]
    assert "GATK 4.7" in result["error"]["message"]
    assert not (tmp_path / "ref" / "ref.dict").exists()
    assert not (tmp_path / "out" / "gatk.vcf.gz").exists()
    assert list((tmp_path / "scratch").iterdir()) == []


def test_gatk_region_cant_be_read_as_a_samtools_option(genome, gatk, tmp_path, capsys):
    # Review of PR #454: a region "-o<file>" made the CRAM cut write <file>.
    gatk.set(reads_cram=False)
    ref = _reference_copy(genome, tmp_path / "ref")
    written = tmp_path / "written.sam"
    result, _ = _gatk(capsys, genome, genome.cram, ref, tmp_path / "out", None,
                      f"--region=-o{written}")  # fmt: skip
    assert result["status"] == "failed"
    assert not written.exists()


def test_gatk_intervals_dont_follow_a_link_in_the_outdir(
    genome, gatk, tmp_path, capsys
):
    # Review of PR #454: the intervals were written to a fixed name in the
    # outdir, through any link already there.
    outdir = tmp_path / "out"
    outdir.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("keep")
    (outdir / "gatk_region.intervals").symlink_to(target)
    ref = _reference_copy(genome, tmp_path / "ref")
    result, _ = _gatk(capsys, genome, genome.bam, ref, outdir, tmp_path / "scratch",
                      "-r", f"chr1:{SNP - 100}-{SNP + 100}")  # fmt: skip
    assert result["status"] == "ok", result["error"]
    assert vcf_calls(outdir / "gatk.vcf.gz") == [genome.call]
    assert target.read_text() == "keep"


@pytest.mark.parametrize("stage", ["gatk", "conversion"])
def test_gatk_failure_on_a_cram_region_cleans_up_without_a_cram_hint(
    genome, gatk, tmp_path, capsys, stage
):
    """Whether GATK or the CRAM-to-BAM cut fails (a reference that doesn't match
    the CRAM fails its MD5 check part-way), the partial or temporary BAM, its
    index, the intervals and GATK's temp dir are removed. GATK read a BAM, so
    no CRAM hint is given (#1034: it blamed CRAM 3.1 for any failure)."""
    gatk.set(fail="unexpected CRAM container")
    ref = _reference_copy(genome, tmp_path / "ref")
    if stage == "conversion":
        seq = "".join(Path(ref).read_text().splitlines()[1:])
        write_fasta(Path(ref), {"chr1": seq[::-1]})
        run_command(["samtools", "faidx", ref])
    outdir = tmp_path / "out"
    result, log = _gatk(capsys, genome, genome.cram, ref, outdir, None,
                   "-r", f"chr1:{SNP - 100}-{SNP + 100}")  # fmt: skip

    assert result["status"] == "failed"
    assert "GATK failed" in result["error"]["message"]
    assert "Hint:" not in log
    assert (gatk.runs() == []) == (stage == "conversion")
    assert list(outdir.iterdir()) == []
    assert list((tmp_path / "systemp").iterdir()) == []


@pytest.mark.parametrize(
    ("version", "error", "hint"),
    [
        ("3.1", None, "can't read CRAM 3.1"),
        ("3.0", "htsjdk.samtools.cram.CRAMException: Attempt to create a bai entry",
         "couldn't read the CRAM: Attempt to create a bai entry"),
    ],
)  # fmt: skip
def test_1034_gatk_cram_hint_names_htsjdks_error_not_cram_31(
    genome, gatk, tmp_path, capsys, version, error, hint
):
    """#1034 (vcf gatk rejects 1000 Genomes CRAM indexes): the hint blamed CRAM
    3.1 for a CRAM 3.0 that htsjdk refused; it names 3.1 only for a 3.1 file."""
    gatk.set(reads_cram=version == "3.0", fail=error)
    ref = _reference_copy(genome, tmp_path / "ref")
    cram = str(tmp_path / "reads.cram")
    run_command(["samtools", "view", "-C", "-T", ref,
                 "--output-fmt-option", f"version={version}", "-o", cram, genome.bam])  # fmt: skip
    run_command(["samtools", "index", cram])
    result, log = _gatk(capsys, genome, cram, ref, tmp_path / "out", tmp_path / "s")

    assert result["status"] == "failed"
    assert hint in log
    assert bool(re.search(r"CRAM (version )?3\.1", log)) == (version == "3.1")


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs a folder the user can't write to",
)
def test_gatk_reports_a_dict_it_cannot_write(genome, gatk, tmp_path, capsys):
    ref = _reference_copy(genome, tmp_path / "ref")
    (tmp_path / "ref").chmod(0o555)
    try:
        result, log = _gatk(capsys, genome, genome.bam, ref, tmp_path / "out",
                       tmp_path / "scratch")  # fmt: skip
    finally:
        (tmp_path / "ref").chmod(0o755)
    assert result["status"] == "failed"
    assert "Failed to generate GATK dict" in result["error"]["message"]
    assert gatk.runs() == []


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_gatk_on_windows_gets_native_paths_and_a_numeric_warning(
    genome, gatk, tmp_path, monkeypatch, capsys, platform
):
    """Windows GATK reads only native paths (a bare R:/x is a URI to Java), here
    with spaces and a bgzipped reference; its native kernels can change numbers.
    GATK can't start from a temp dir with a space, which every Windows user with
    a space in their name has, so it gets the dir's 8.3 short path there."""
    monkeypatch.setattr(paths, "sys", Namespace(platform=platform))
    monkeypatch.setattr(callers, "sys", Namespace(platform=platform))
    gatk.set(windows=platform == "win32")
    scratch = tmp_path / "scratch"
    if platform == "win32":
        scratch = tmp_path / "Scratch space"
        (tmp_path / "SCRATC~1").symlink_to(scratch, target_is_directory=True)

        def short_path(path, buffer, size):
            buffer.value = path.replace("Scratch space", "SCRATC~1")
            return len(buffer.value)

        kernel32 = Namespace(GetShortPathNameW=short_path)
        monkeypatch.setattr(
            ctypes, "windll", Namespace(kernel32=kernel32), raising=False
        )
    ref = _reference_copy(genome, tmp_path / "Reference genome", gzip=True)
    # A missing .dict would be written by the host's samtools at a native path.
    run_command(["samtools", "dict", "-o", str(tmp_path / "Reference genome" / "ref.dict"), ref])  # fmt: skip
    outdir = tmp_path / "Output data"
    result, log = _gatk(capsys, genome, genome.bam, ref, outdir, scratch,
                   "-r", f"chr1:{SNP - 100}-{SNP + 100}")  # fmt: skip

    assert result["status"] == "ok", result["error"]
    assert vcf_calls(outdir / "gatk.vcf.gz") == [genome.call]
    assert ("compression remains Java-based" in log) == (platform == "win32")
    assert "temp directory has a space" not in log
    assert list(scratch.iterdir()) == []
