"""Target-CNV screening (#1285) with real samtools and synthetic depth."""

import csv
import struct
import subprocess
from collections import defaultdict
from importlib.resources import files
from pathlib import Path
from shutil import copyfile

import pytest

from tests.htslib_helpers import needs_tools, run_wgsextract, sam_to_bam
from wgsextract_cli import api
from wgsextract_cli.core.panel import load_annotations

pytestmark = needs_tools("samtools", "bgzip")

TARGETS = (
    ("1", 2_000_000, "deletion", 0),
    ("1", 2_000_100, "diploid", 10),
    ("1", 2_000_200, "duplication", 15),
    ("1", 2_001_000, "sparse_gc_control", 20),
    ("X", 20_000_000, "X_haploid", 10),
    ("Y", 7_002_000, "Y_haploid", 10),
    ("Y", 5_000_000, "Y_masked", 0),
    ("X", 2_000_000, "PAR", 20),
    ("5", 70_925_000, "custom_label", 20),
)
CURATED_TARGETS = (
    ("chr1", 109_687_816, 109_693_745, "deletion_alias", 0),
    ("chr1", 25_272_485, 25_330_445, "RHD", 10),
    ("chr4", 68_537_172, 68_576_322, "UGT2B17", 15),
    ("chr1", 109_687_816, 109_687_916, "GSTM1", 0),
)
BASES = "ACGT" * 25
LOW_GC = "AACT" * 25
HIGH_GC = "GCGT" * 25
CURATED_BASES = "GCGT" + "ACGT" * 24


def _fake_fasta(path, lengths, patches):
    # Generate a small BGZF file with the true chromosome lengths and only
    # synthetic bases. Precompress the blocks for runs of Ns once.
    def wrap(bases):
        return b"".join(bases[i : i + 80] + b"\n" for i in range(0, len(bases), 80))

    block = b"N" * 64_000
    bgzf_block = subprocess.check_output(["bgzip", "-c"], input=wrap(block))
    eof = bgzf_block[-28:]
    bgzf_block = bgzf_block[:-28]
    fai = []
    gzi = []
    uncompressed = 0
    with path.open("wb") as handle:
        for chrom, length in lengths.items():
            if chrom not in patches:
                continue
            rows = sorted(patches[chrom].items())
            header = f">{chrom}\n".encode()
            fai.append(f"{chrom}\t{length}\t{uncompressed + len(header)}\t80\t81\n")
            index = 0
            for start in range(0, length, len(block)):
                if uncompressed:
                    gzi.append((handle.tell(), uncompressed))
                end = min(start + len(block), length)
                if (
                    start
                    and end != length
                    and (index == len(rows) or rows[index][0] >= end)
                    and (
                        not index
                        or rows[index - 1][0] + len(rows[index - 1][1]) <= start
                    )
                ):
                    handle.write(bgzf_block)
                    uncompressed += 64_800
                    continue
                part = bytearray(block[: end - start])
                j = max(0, index - 1)
                while j < len(rows) and rows[j][0] < end:
                    pos, bases = rows[j]
                    left, right = max(start, pos), min(end, pos + len(bases))
                    if left < right:
                        part[left - start : right - start] = bases[
                            left - pos : right - pos
                        ]
                    j += 1
                index = j
                data = (header if not start else b"") + wrap(part)
                handle.write(subprocess.check_output(["bgzip", "-c"], input=data)[:-28])
                uncompressed += len(data)
        handle.write(eof)
    Path(f"{path}.fai").write_text("".join(fai), newline="\n")
    Path(f"{path}.gzi").write_bytes(
        struct.pack("<Q", len(gzi))
        + b"".join(struct.pack("<QQ", *offsets) for offsets in gzi)
    )


@pytest.fixture(scope="module")
def cnv_fixture(tmp_path_factory):
    path = tmp_path_factory.mktemp("target-cnv")
    lengths = load_annotations("hg38").contigs
    patches = defaultdict(dict)
    autosomal_reads = []
    sex_regions = []
    baseline_text = (
        files("wgsextract_cli")
        .joinpath("assets", "target_cnv", "hg38.baseline.bed")
        .read_text(encoding="utf-8")
    )

    def reads(group, chrom, start, seq, depth):
        return [
            f"{chrom}_{start}_{i}\t0\t{chrom}\t{start + 1}\t60\t100M\t*\t0\t0\t"
            f"{seq}\t{'I' * 100}\tRG:Z:{group}"
            for i in range(depth)
        ]

    for number, row in enumerate(baseline_text.splitlines()[1:]):
        chrom, start_s, end_s = row.split()
        start, end = int(start_s), int(end_s)
        seq, depth = (LOW_GC, 10) if number % 2 else (BASES, 20)
        if number < 5:
            seq, depth = HIGH_GC, 20
        patches[chrom][start] = (seq * 10).encode()
        # The high-GC bin has only two well-covered windows, despite WGS-like
        # breadth across the rest of the baseline.
        for offset in range(start, start + 500 if number < 3 else end, 100):
            autosomal_reads.extend(reads("r", chrom, offset, seq, depth))
    for chrom, start, name, depth in TARGETS:
        seq = LOW_GC if chrom == "1" else BASES
        if name == "sparse_gc_control":
            seq = HIGH_GC
        patches[f"chr{chrom}"][start] = seq.encode()
        destination = sex_regions if chrom in {"X", "Y"} else autosomal_reads
        destination.extend(reads("r", f"chr{chrom}", start, seq, depth))
    for chrom, start, end, name, depth in CURATED_TARGETS:
        if name == "GSTM1":  # The short interval is part of deletion_alias.
            continue
        patches[chrom][start] = (CURATED_BASES * ((end - start + 99) // 100))[
            : end - start
        ].encode()
        for offset in range(start, end, 100):
            autosomal_reads.extend(reads("r", chrom, offset, CURATED_BASES, depth))

    windows = {
        "7": (1, 159_345_973),
        "19": (1, 58_617_616),
        "X": (2_781_480, 155_701_382),
        "Y": (2_781_480, 26_673_214),
    }
    for chrom, (lo, hi) in windows.items():
        for part in (1, 3, 5, 7, 9):
            left = lo - 1 + (hi - lo + 1 - 10_000) * part // 10
            depth = 8 if chrom in {"7", "19"} else 4
            for start in range(left, left + 10_000, 100):
                sex_regions.extend(reads("r", f"chr{chrom}", start, BASES, depth))
    header = [f"@SQ\tSN:{c}\tLN:{lengths[c]}" for c in patches]
    header.append("@RG\tID:r\tSM:FAKE\tPL:ILLUMINA")
    reference = path / "reference.fa.gz"
    _fake_fasta(reference, lengths, patches)
    bed = path / "targets.bed"
    bed.write_text(
        "".join(
            [f"{c}\t{s}\t{s + 100}\t{name}\n" for c, s, name, _ in TARGETS]
            + [f"{c}\t{s}\t{e}\t{name}\n" for c, s, e, name, _ in CURATED_TARGETS]
        ),
        newline="\n",
    )
    return (
        Path(sam_to_bam(header + autosomal_reads + sex_regions, path / "xy.bam")),
        Path(sam_to_bam(header + autosomal_reads, path / "unknown-sex.bam")),
        reference,
        bed,
    )


def _calls(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["name"]: row for row in csv.DictReader(handle, delimiter="\t")}


def test_1285_depth_screen_gc_mask_and_inferred_sex(cnv_fixture, tmp_path):
    bam, _, ref, bed = cnv_fixture
    result = api.genotyping.target_cnv(
        api.TypingTargetCnvParams(
            input=bam, bed=bed, ref=ref, outdir=tmp_path, threads=1
        )
    )
    calls = _calls(tmp_path / "xy.target-cnv.tsv")
    assert calls["deletion"]["copy_number"] == "0"
    assert calls["diploid"]["copy_number"] == "2"
    assert calls["duplication"]["copy_number"] == "3+"
    assert calls["duplication"]["confidence"] == "high"
    assert calls["sparse_gc_control"]["reason"] == "no_gc_baseline"
    assert calls["sparse_gc_control"]["copy_number"] == ""
    for name in ("X_haploid", "Y_haploid"):
        assert calls[name]["expected_copy_number"] == "1"
        assert calls[name]["copy_number"] == "1"
    assert calls["PAR"]["copy_number"] == ""
    assert calls["custom_label"]["state"] == "specialist_caller_required"
    assert calls["custom_label"]["specialist"] == "typing smn"
    assert calls["custom_label"]["copy_number"] == ""
    assert result.metrics["effective_sex"] == "male"
    assert result.metrics["target_cnv_called"] == 7
    assert result.metrics["target_cnv_curated_called"] == 2
    assert any(w.code == "target_cnv_screening_only" for w in result.warnings)


def test_1285_curated_calls_are_reported_but_ambiguous_targets_and_unknown_y_are_not(
    cnv_fixture, tmp_path
):
    _, bam, ref, bed = cnv_fixture
    run_wgsextract(
        "typing",
        "target-cnv",
        "-i",
        str(bam),
        "--ref",
        str(ref),
        "--bed",
        str(bed),
        "-o",
        str(tmp_path),
        "-t",
        "1",
    )
    calls = _calls(tmp_path / "unknown-sex.target-cnv.tsv")
    assert calls["Y_haploid"]["reason"] == "sex_unknown"
    assert calls["Y_haploid"]["copy_number"] == ""
    assert calls["duplication"]["copy_number"] == "3+"
    for name, number in (("deletion_alias", "0"), ("RHD", "1")):
        assert calls[name]["state"] == "called"
        assert calls[name]["reason"] == "masked_region_curated_call"
        assert calls[name]["copy_number"] == number
        assert calls[name]["confidence"] == "medium"
    assert "RHtyper" in calls["RHD"]["specialist"]
    for name in ("UGT2B17", "GSTM1"):
        assert calls[name]["state"] == "specialist_caller_required"
        assert calls[name]["copy_number"] == ""


def test_1285_masked_y_in_declared_female_is_not_applicable(cnv_fixture, tmp_path):
    _, bam, ref, bed = cnv_fixture
    female = tmp_path / "female.bam"
    copyfile(bam, female)
    copyfile(f"{bam}.bai", f"{female}.bai")
    api.sample.set_overrides(api.SampleSetParams(input=female, sex="female"))
    result = api.genotyping.target_cnv(
        api.TypingTargetCnvParams(
            input=female, bed=bed, ref=ref, outdir=tmp_path, threads=1
        )
    )
    call = _calls(tmp_path / "female.target-cnv.tsv")["Y_masked"]
    assert result.metrics["effective_sex"] == "female"
    assert call["expected_copy_number"] == "0"
    assert call["state"] == "not_applicable"
    assert call["copy_number"] == ""


def test_1285_unknown_build_has_no_bundled_baseline(tmp_path):
    bam = sam_to_bam(["@SQ\tSN:chr1\tLN:1000"], tmp_path / "other.bam")
    bed = tmp_path / "targets.bed"
    bed.write_text("chr1\t0\t100\n", newline="\n")
    with pytest.raises(
        api.CommandFailed, match="No target-CNV baseline for build unknown"
    ):
        api.genotyping.target_cnv(
            api.TypingTargetCnvParams(input=Path(bam), bed=bed, outdir=tmp_path)
        )
