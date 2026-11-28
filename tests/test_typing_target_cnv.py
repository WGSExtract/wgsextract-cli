"""Target-CNV screening (#1285) with real samtools and synthetic depth."""

import csv
import gzip
import json
import struct
import subprocess
from collections import defaultdict
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from shutil import copyfile
from typing import Any

import pytest

from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    needs_tools,
    run_wgsextract,
    sam_to_bam,
)
from wgsextract_cli import api
from wgsextract_cli.core.panel import load_annotations

pytestmark = needs_tools("samtools", "bgzip")

TARGETS = (
    ("1", 2_000_000, "deletion", 0),
    ("1", 2_000_100, "diploid", 10),
    ("1", 2_000_200, "duplication", 15),
    ("1", 2_000_300, "heterozygous_deletion", 5),
    ("1", 2_001_000, "sparse_gc_control", 20),
    ("X", 20_000_000, "X_haploid", 10),
    ("Y", 7_002_000, "Y_haploid", 10),
    ("Y", 5_000_000, "Y_masked", 0),
    ("X", 2_000_000, "PAR", 20),
    ("5", 70_925_000, "custom_label", 20),
    ("7", 5_973_398, "masked_pms2", 20),
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
PANEL_LOW_GC = "GC" + LOW_GC[2:]
PANEL_MEDIUM_GC = "GC" + BASES[2:]
PANEL_TARGETS = {
    "HES4": (PANEL_LOW_GC, 0), "ISG15": (PANEL_LOW_GC, 5), "TNFRSF18": (PANEL_LOW_GC, 10),
    "TNFRSF4": (PANEL_LOW_GC, 15), "B3GALT6": (PANEL_LOW_GC, 0), "PUSL1": (PANEL_LOW_GC, 5),
    "FNDC10": (PANEL_LOW_GC, 10), "CALML6": (PANEL_LOW_GC, 15), "TMEM52": (PANEL_LOW_GC, 10),
    "HES5": (HIGH_GC, 20), "ACTRT2": (PANEL_MEDIUM_GC, 15), "HES3": (BASES, 20),
    "ERRFI1": ("ATCG" * 25, 20),
}  # fmt: skip


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

    def reads(group: str, chrom: str, start: int, seq: str, depth: int) -> list[str]:
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
        elif number in range(6, 16, 2):
            seq = "AA" + BASES[2:]
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
    for symbol, (seq, depth) in PANEL_TARGETS.items():
        (locus,) = load_annotations("hg38").loci[symbol]
        start, end = locus.start, locus.end
        patches[locus.chrom][start] = (seq * ((end - start + 99) // 100))[
            : end - start
        ].encode()
        for offset in range(start, end, 100):
            autosomal_reads.extend(reads("r", locus.chrom, offset, seq, depth))

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


def _calls(path: Path) -> dict[str, dict[str, str]]:
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
    het = calls["heterozygous_deletion"]
    assert (het["expected_copy_number"], het["copy_number"]) == ("2", "1")
    assert (het["state"], het["reason"]) == ("called", "")
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
    pms2 = calls["masked_pms2"]
    assert pms2["state"] == "specialist_caller_required"
    assert pms2["specialist"] == "typing paraphase --genes pms2"
    assert pms2["copy_number"] == ""
    assert result.metrics["effective_sex"] == "male"
    assert result.metrics["target_cnv_called"] == 8
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


DepthFixture = tuple[Path, Path, Path, Path]
PanelCnvFixture = tuple[Path, Path, Path, Path, Path]


@pytest.fixture(scope="module")
def panel_cnv_fixture(
    cnv_fixture: DepthFixture, tmp_path_factory: pytest.TempPathFactory
) -> PanelCnvFixture:
    root = tmp_path_factory.mktemp("panel-cnv")
    _, bam, ref, _ = cnv_fixture
    annotations = load_annotations("hg38")
    loci = sorted(
        (annotations.loci[gene][0] for gene in PANEL_TARGETS), key=lambda row: row.start
    )
    bed = root / "whole-genes.bed"
    (masked,) = annotations.loci["PMS2"]
    bed.write_text(
        "".join(
            f"{r.chrom}\t{r.start}\t{r.end}\t{r.symbol}\n" for r in [*loci, masked]
        ),
        encoding="utf-8",
    )
    result = api.genotyping.target_cnv(
        api.TypingTargetCnvParams(input=bam, bed=bed, ref=ref, outdir=root, threads=1)
    )
    assert result.manifest is not None
    tsv = Path(next(f.path for f in result.outputs if f.role == "target_cnv"))
    calls = root / "small.vcf"
    base = """##fileformat=VCFv4.2
##contig=<ID=chr1,length=248956422>
##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">
##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">
"""
    columns = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tFAKE\n"
    hits = {"TNFRSF18": "1/1", "PUSL1": "0/1"}
    calls.write_text(
        base
        + columns
        + "".join(
            f"chr1\t{r.start + 1}\t.\tA\tG\t30\tPASS\tCSQ=G|stop_gained|HIGH|{r.symbol}"
            f"\tGT:DP:GQ\t{hits[r.symbol]}:30:60\n"
            for r in loci
            if r.symbol in hits
        ),
        encoding="utf-8",
    )
    gvcf = root / "small.g.vcf"
    gvcf.write_text(
        base
        + '##ALT=<ID=NON_REF,Description="Any ALT">\n'
        + '##INFO=<ID=END,Number=1,Type=Integer,Description="Block end">\n'
        + columns
        + "".join(
            f"chr1\t{r.start + 1}\t.\tA\t<NON_REF>\t.\tPASS\tEND={r.start + 100}"
            "\tGT:DP:GQ:MIN_DP\t0/0:30:60:30\n"
            for r in loci
        ),
        encoding="utf-8",
    )
    model = root / "mane.tsv"
    model.write_text(
        "#contig\tchr1\t248956422\n"
        "gene\ttranscript\ttag\tstrand\texon\texons\tchrom\tstart\tend\tcds_start\tcds_end\tlimitations\n"
        + "".join(
            f"{r.symbol}\tNM_{i}\tMANE Select\t+\t1\t1\tchr1\t{r.start + 1}\t{r.start + 100}"
            f"\t{r.start + 1}\t{r.start + 100}\t\n"
            for i, r in enumerate(loci)
        ),
        encoding="utf-8",
    )
    return (
        tsv,
        Path(result.manifest),
        Path(bgzip_index(calls)),
        Path(bgzip_index(gvcf)),
        model,
    )


def _panel_with_cnv(path: Path, groups: list[dict[str, Any]]) -> Path:
    panel = path / "cnv.json"
    panel.write_text(
        json.dumps(
            {
                "schema": "wgsextract.panel/1",
                "id": "copy-panel",
                "name": "Research copy panel",
                "version": "1",
                "build": "GRCh38",
                "defaults": {"qualify": [{"impacts": ["HIGH"], "weight": 0.5}]},
                "groups": groups,
            }
        ),
        encoding="utf-8",
    )
    return panel


@needs_htslib
def test_1664_joint_panel_null_uses_same_calls_and_weights_on_both_sides(
    panel_cnv_fixture: PanelCnvFixture, tmp_path: Path
) -> None:
    tsv, manifest, calls, gvcf, model = panel_cnv_fixture
    panel = _panel_with_cnv(
        tmp_path,
        [
            {
                "id": "copies",
                "genes": ["HES4", "ISG15", "TNFRSF18", "TNFRSF4"],
                "deletion_weight": 0.5,
                "duplication_weight": 0.25,
            }
        ],
    )
    assert (
        api.panel.check(api.PanelCheckParams(panel=panel, build="hg38")).status == "ok"
    )
    params = api.PanelAuditParams(
        panel=panel,
        vcf_input=calls,
        gvcf=gvcf,
        exons=model,
        outdir=tmp_path,
        research_panel=True,
        target_cnv=tsv,
        target_cnv_manifest=manifest,
        null_sets=100,
    )
    result = api.panel.audit(params)
    report = json.loads(
        Path(
            next(f.path for f in result.outputs if f.role == "panel_audit")
        ).read_text()
    )
    assert report["research_only"] is True
    (group,) = report["groups"]
    assert [g["target_cnv"]["copy_number"] for g in group["genes"]] == [
        "0",
        "1",
        "2",
        "3+",
    ]
    stats = group["cnv_calibration"]
    assert stats["state"] == "calibrated"
    assert stats["observed_small_variant_burden"] == 1
    assert stats["observed_deletion_burden"] == 1.5
    assert stats["observed_duplication_burden"] == 0.25
    assert stats["observed_combined_burden"] == 2.75
    # 33 of the 126 assignments of four labels to nine genes reach 2.75.
    assert stats["upper_tail_p"] == pytest.approx(33 / 126)
    assert stats["z_score"] > 0
    assert stats["null_mean_deletion_burden"] > 0
    assert stats["null_mean_duplication_burden"] > 0
    assert stats["strata"][0]["control_genes"] == 5
    assert stats["callability"]["excluded_control_counts"] == {
        "low_confidence": 1,
        "masked_or_specialist": 1,
        "no_gc_baseline": 1,
    }
    rerun = api.panel.audit(replace(params, null_sets=200, outdir=tmp_path / "rerun"))
    rerun_report = Path(next(f.path for f in rerun.outputs if f.role == "panel_audit"))
    rerun_stats = json.loads(rerun_report.read_text())["groups"][0]["cnv_calibration"]
    assert rerun_stats["upper_tail_p"] == stats["upper_tail_p"]


@needs_htslib
def test_1664_incomplete_targets_and_unmatched_controls_withhold_joint_null(
    panel_cnv_fixture: PanelCnvFixture, tmp_path: Path
) -> None:
    tsv, manifest, calls, gvcf, model = panel_cnv_fixture
    panel = _panel_with_cnv(
        tmp_path,
        [
            {
                "id": "missing",
                "genes": ["HES5", "ACTRT2", "CORT", "PMS2"],
                "deletion_weight": 1,
            },
            {"id": "different-gc", "genes": ["HES3"], "duplication_weight": 1},
        ],
    )
    result = api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=calls,
            gvcf=gvcf,
            exons=model,
            outdir=tmp_path,
            research_panel=True,
            target_cnv=tsv,
            target_cnv_manifest=manifest,
            null_sets=100,
        )
    )
    report = json.loads(
        Path(
            next(f.path for f in result.outputs if f.role == "panel_audit")
        ).read_text()
    )
    first, second = [group["cnv_calibration"] for group in report["groups"]]
    assert first["callability"]["excluded_targets"] == {
        "HES5": "no_gc_baseline",
        "ACTRT2": "low_confidence",
        "CORT": "missing_target",
        "PMS2": "masked_or_specialist",
    }
    assert first["reason"] == "incomplete_target_evidence"
    assert second["reason"] == "insufficient_matched_controls"
    # The displayed GC fractions tie at four decimals, but ERRFI1 is just
    # below the caller's bin boundary and is not a matched HES3 control.
    depth = _calls(tsv)
    assert depth["ERRFI1"]["gc_fraction"] == depth["HES3"]["gc_fraction"]
    assert second["strata"][0]["control_genes"] == 0
    for stats in (first, second):
        assert (
            not {"observed_combined_burden", "z_score", "upper_tail_p"} & stats.keys()
        )
    assert any(w.code == "panel_cnv_calibration_underpowered" for w in result.warnings)


@needs_htslib
def test_1664_callable_cnv_without_callable_cds_withholds_combined_null(
    panel_cnv_fixture: PanelCnvFixture, tmp_path: Path
) -> None:
    tsv, manifest, calls, gvcf, model = panel_cnv_fixture
    (locus,) = load_annotations("hg38").loci["HES4"]
    missing_cds = tmp_path / "missing.g.vcf"
    with gzip.open(gvcf, "rt", encoding="utf-8") as source:
        missing_cds.write_text(
            "".join(
                line
                for line in source
                if not line.startswith(f"chr1\t{locus.start + 1}\t")
            ),
            encoding="utf-8",
        )
    group = {"id": "copy", "genes": ["HES4"]}
    weighted_panel = _panel_with_cnv(tmp_path, [{**group, "deletion_weight": 1}])
    params = api.PanelAuditParams(
        panel=weighted_panel,
        vcf_input=calls,
        gvcf=Path(bgzip_index(missing_cds)),
        exons=model,
        outdir=tmp_path,
        research_panel=True,
        target_cnv=tsv,
        target_cnv_manifest=manifest,
        null_sets=100,
    )
    result = api.panel.audit(params)
    report = Path(next(f.path for f in result.outputs if f.role == "panel_audit"))
    (observed,) = json.loads(report.read_text())["groups"]
    stats = observed["cnv_calibration"]
    assert observed["genes"][0]["target_cnv"]["copy_number"] == "0"
    assert stats["callability"]["joint_excluded_targets"] == {"HES4": "no_callable_cds"}
    assert "upper_tail_p" not in stats and "observed_combined_burden" not in stats

    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    plain = api.panel.audit(
        replace(
            params,
            panel=_panel_with_cnv(plain_dir, [group]),
            outdir=plain_dir,
            target_cnv=None,
            target_cnv_manifest=None,
        )
    )
    plain_report = Path(next(f.path for f in plain.outputs if f.role == "panel_audit"))
    (unweighted,) = json.loads(plain_report.read_text())["groups"]
    assert unweighted["calibration"] == observed["calibration"]


@needs_htslib
def test_1664_joint_audit_rejects_mismatched_cnv_provenance(
    panel_cnv_fixture: PanelCnvFixture, tmp_path: Path
) -> None:
    tsv, manifest, calls, _, _ = panel_cnv_fixture
    panel = _panel_with_cnv(
        tmp_path, [{"id": "copy", "genes": ["HES4"], "deletion_weight": 1}]
    )
    params = api.PanelAuditParams(
        panel=panel, vcf_input=calls, research_panel=True, outdir=tmp_path
    )
    message = "--target-cnv"
    missing = api.panel.audit(params, check=False)
    assert missing.exit_code == 2 and missing.error and message in missing.error.message

    original = json.loads(manifest.read_text())
    altered = tmp_path / "altered.manifest.json"
    for field in ("sample", "build", "sha256"):
        doc = json.loads(json.dumps(original))
        if field == "build":
            doc["metrics"]["target_cnv_build"] = "hg19"
        else:
            output = next(f for f in doc["outputs"] if f["role"] == "target_cnv")
            output["metrics"][field] = "incorrect"
        altered.write_text(json.dumps(doc))
        result = api.panel.audit(
            replace(params, target_cnv=tsv, target_cnv_manifest=altered),
            check=False,
        )
        text = "checksum" if field == "sha256" else field
        assert result.exit_code == 2 and result.error and text in result.error.message


@needs_htslib
@pytest.mark.parametrize("partial", [False, True])
def test_1664_joint_audit_rejects_ambiguous_whole_gene_designs(
    cnv_fixture: DepthFixture,
    panel_cnv_fixture: PanelCnvFixture,
    tmp_path: Path,
    partial: bool,
) -> None:
    _, bam, ref, _ = cnv_fixture
    _, _, calls, _, _ = panel_cnv_fixture
    (locus,) = load_annotations("hg38").loci["HES4"]
    row = f"{locus.chrom}\t{locus.start}\t{locus.end - int(partial)}\t{locus.symbol}\n"
    bed = tmp_path / "targets.bed"
    bed.write_text(row if partial else row * 2, encoding="utf-8")
    depth = api.genotyping.target_cnv(
        api.TypingTargetCnvParams(input=bam, ref=ref, bed=bed, outdir=tmp_path)
    )
    assert depth.manifest
    panel = _panel_with_cnv(
        tmp_path, [{"id": "copy", "genes": ["HES4"], "deletion_weight": 1}]
    )
    audit = api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=calls,
            research_panel=True,
            outdir=tmp_path,
            target_cnv=Path(
                next(f.path for f in depth.outputs if f.role == "target_cnv")
            ),
            target_cnv_manifest=Path(depth.manifest),
        ),
        check=False,
    )
    assert audit.exit_code == 2 and audit.error
    assert ("exact 0-based" if partial else "duplicate") in audit.error.message
