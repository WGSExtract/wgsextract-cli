import argparse
import gzip
import json
import logging
import platform as host_platform
import random
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.htslib_helpers import needs_tools, run_wgsextract, sam_to_bam, write_fasta
from tests.prepared_data import sparse_truncate
from wgsextract_cli import api
from wgsextract_cli.commands import align, bam, realign
from wgsextract_cli.commands.vcf import basic, callers, contig_calling
from wgsextract_cli.core import alignment_memory
from wgsextract_cli.core import resource_estimates as resources
from wgsextract_cli.core.alignment_metadata import get_bam_header

GIB = 1024**3
H2_READS = 679_639_896


def test_arm_deepvariant_inference_floor_survives_fewer_shards_and_small_regions(
    source, tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(host_platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 24 * GIB)
    options = {
        "input_paths": [str(source)],
        "output_dir": str(tmp_path),
        "region": "chr20:1-1000",
    }
    one = resources.estimate_resources("deepvariant", threads="1", **options)
    sixteen = resources.estimate_resources("deepvariant", threads="16", **options)
    assert one.resources[0].estimated_bytes == sixteen.resources[0].estimated_bytes
    assert one.resources[0].status == "LOW"
    with caplog.at_level(logging.INFO):
        resources.report_resources(one)
    assert "LOW" in caplog.text
    assert "inference memory floor" in caplog.text
    many = resources.estimate_resources("deepvariant", threads="32", **options)
    assert many.resources[0].estimated_bytes > sixteen.resources[0].estimated_bytes
    mitigated = resources.estimate_resources(
        "deepvariant", threads="8", inference_batch_size=256, **options
    )
    assert mitigated.resources[0].estimated_bytes < one.resources[0].estimated_bytes
    assert mitigated.resources[0].status == "OK"


# hs38DH: GRCh38's primary contigs, then 3,341 alt/decoy/HLA contigs (one here).
HS38_PRIMARY = {
    "chr1": 248956422, "chr2": 242193529, "chr3": 198295559, "chr4": 190214555,
    "chr5": 181538259, "chr6": 170805979, "chr7": 159345973, "chr8": 145138636,
    "chr9": 138394717, "chr10": 133797422, "chr11": 135086622,
    "chr12": 133275309, "chr13": 114364328, "chr14": 107043718,
    "chr15": 101991189, "chr16": 90338345, "chr17": 83257441, "chr18": 80373285,
    "chr19": 58617616, "chr20": 64444167, "chr21": 46709983, "chr22": 50818468,
    "chrX": 156040895, "chrY": 57227415, "chrM": 16569,
}  # fmt: skip
HS38DH = {**HS38_PRIMARY, "decoys": 129060516}


def _whole_genome(tmp_path):
    ref = tmp_path / "hs38DH.fa"
    ref.write_text(">x\n")
    Path(f"{ref}.fai").write_text(
        "".join(f"{name}\t{length}\t0\t60\t61\n" for name, length in HS38DH.items())
    )
    return resources.dv_calling_regions(HS38DH, None), ref


def test_dv_partitions_reproduce_the_173_whole_genome_run(tmp_path):
    regions, _ref = _whole_genome(tmp_path)
    # postprocess_variants' logged partitions: chr1-3, 4-7, 8-12, 13-20, chr21+.
    assert resources.dv_partition_bases(regions, 5) == [
        689445510, 701904766, 685692706, 700430089, 439873846
    ]  # fmt: skip
    assert resources.dv_partition_bases([("chr1", 0, 3)], 4) is None


def test_whole_genome_deepvariant_budgets_postprocess_on_x86(
    source, tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(host_platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 64 * GIB)
    _regions, ref = _whole_genome(tmp_path)
    options = {
        "input_paths": [str(source)],
        "output_dir": str(tmp_path),
        "reference": str(ref),
        "threads": "5",
    }
    estimate = resources.estimate_resources("deepvariant", **options)
    stage = estimate.resources[0].components["largest_sequential_stage"]
    # #173 measured 25-30 GB; workers were killed before their peaks.
    assert 30 * GIB < stage < 40 * GIB
    assert any("5 workers over 5 partitions" in note for note in estimate.notes)
    regional = resources.estimate_resources("deepvariant", region="chr20", **options)
    assert regional.resources[0].components["largest_sequential_stage"] == 16 * GIB
    exome = resources.estimate_resources(
        "deepvariant", deepvariant_model="WES", **options
    )
    assert exome.resources[0].components["largest_sequential_stage"] == 16 * GIB


@pytest.mark.parametrize(
    ("threads", "available", "cpus", "partitions"),
    [
        (5, None, 5, 5),  # Unknown RAM: DeepVariant's default.
        (5, 64 * GIB, 5, 5),
        (5, 16 * GIB, 5, 19),  # More, smaller partitions keep every worker.
        (16, 12 * GIB, 10, 118),  # Fewer workers once partitions stop shrinking.
    ],
)
def test_postprocess_plan_fits_available_memory(
    tmp_path, threads, available, cpus, partitions
):
    regions, _ref = _whole_genome(tmp_path)
    plan = resources.plan_dv_postprocess(regions, threads, available)
    assert (plan.cpus, plan.partitions) == (cpus, partitions)
    assert plan.fits is (None if available is None else True)
    if available is not None:
        assert plan.peak_bytes <= available - GIB


def test_postprocess_plan_without_num_partitions_only_lowers_workers(tmp_path):
    regions, _ref = _whole_genome(tmp_path)
    # Fewer workers sort bigger partitions, so whole-genome RAM barely drops.
    plans = [
        resources.plan_dv_postprocess(regions, 5, gib * GIB, partitions=False)
        for gib in (16, 32, 40)
    ]
    assert [(p.cpus, p.partitions, p.fits) for p in plans] == [
        (1, 1, False),
        (1, 1, True),
        (5, 5, True),
    ]


def test_postprocess_plan_reports_when_one_worker_cannot_fit(tmp_path):
    regions, _ref = _whole_genome(tmp_path)
    plan = resources.plan_dv_postprocess(regions, 8, 1 * GIB)
    assert (plan.cpus, plan.fits) == (1, False)
    assert plan.worker_bytes > resources.DV_WORKER_BASE_BYTES


def test_realign_staged_fastqs_are_budgeted_on_the_temporary_volume(
    tmp_path, source, monkeypatch
):
    out = str(tmp_path / "out")
    spill = str(tmp_path / "spill")
    monkeypatch.setattr(resources, "_disk_space", lambda path: (path, 100 * GIB))
    estimate = resources.estimate_resources(
        "realign",
        input_paths=[str(source)],
        output_dir=out,
        temp_dir=spill,
        fastq_dir=spill,
        extract_fastq=True,
        reads=1000,
    )
    disks = {
        row.paths[0]: row
        for row in estimate.resources
        if row.resource == "disk" and row.paths
    }
    assert "extracted_fastqs" in disks[spill].components
    assert "extracted_fastqs" not in disks[out].components


def test_regional_gatk_and_scatter_artifacts_are_budgeted_on_the_selected_volume(
    monkeypatch,
):
    monkeypatch.setattr(resources, "_input_size", lambda _: 100 * 1024**2)
    monkeypatch.setattr(resources, "_disk_space", lambda path: (path, 100 * GIB))
    estimate = resources.estimate_resources(
        "gatk",
        input_paths=["input.cram"],
        output_dir="output",
        temp_dir="scratch",
        gatk_staging_dir="scratch",
        region="chrM",
        gatk_workers=2,
    )
    disks = {
        row.paths[0]: row
        for row in estimate.resources
        if row.resource == "disk" and row.paths
    }
    expected = {"regional_bam", "scatter_parts", "scatter_indexes"}
    assert expected <= disks["scratch"].components.keys()
    assert not expected & disks["output"].components.keys()


def _tiny_bam(tmp_path: Path) -> tuple[Path, Path]:
    """A one-read BAM on a 100 bp chr1, and its reference."""
    seq = "ACGT" * 25
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": seq})
    read = f"r1\t0\tchr1\t1\t60\t20M\t*\t0\t0\t{seq[:20]}\t{'I' * 20}"
    bam_path = tmp_path / "tiny.bam"
    sam_to_bam(["@HD\tVN:1.6\tSO:coordinate", "@SQ\tSN:chr1\tLN:100", read], bam_path)
    return bam_path, ref


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "input.cram"
    source.write_bytes(b"x" * 1200)
    return source


def test_streamed_collation_spill_drains_within_the_temporary_sort_estimate(
    tmp_path, source, monkeypatch
):
    output, scratch = str(tmp_path / "output"), str(tmp_path / "scratch")
    monkeypatch.setattr(resources, "_disk_space", lambda path: (path, 100 * GIB))
    estimate = resources.estimate_resources(
        "realign",
        input_paths=[str(source)],
        output_dir=output,
        temp_dir=scratch,
        reads=1000,
        additional_ram_bytes=64 * 1024**2,
    )
    disks = {
        row.paths[0]: row
        for row in estimate.resources
        if row.resource == "disk" and row.paths
    }
    # Collate's buckets are deleted as they drain while the sorter's spills grow.
    assert set(disks[scratch].components) == {"temporary"}
    assert "temporary" not in disks[output].components


def test_h2_envelope_reuses_memory_model_and_scales_disk_by_input_reads(
    tmp_path, source, monkeypatch
):
    reference = tmp_path / "ref.fa"
    reference.write_text(">chr1\nA\n")
    Path(str(reference) + ".fai").write_text("chr1\t3217346917\t0\t1\t2\n")
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 64 * GIB)
    monkeypatch.setattr(resources, "_disk_space", lambda _: ("same", 500 * GIB))
    options = {
        "input_paths": [str(source)],
        "output_dir": str(tmp_path),
        "reference": str(reference),
        "reads": H2_READS,
        "threads": "6",
        "memory": "256M",
        "output_format": "CRAM",
    }
    estimate = resources.estimate_resources("align", **options)
    doubled = resources.estimate_resources(
        "align", **{**options, "reads": H2_READS * 2}
    )
    expected = alignment_memory.estimate_mapping_memory(
        str(reference), "bwa", threads="6", memory="256M", reads=H2_READS
    )
    memory, disk = estimate.resources
    assert memory.estimated_bytes == expected.total
    assert 18 * GIB < expected.total < 19 * GIB
    assert 70e9 < disk.components["temporary"] < 90e9
    assert 16e9 < disk.components["output"] < 20e9
    assert (
        doubled.resources[1].components["temporary"] == disk.components["temporary"] * 2
    )
    assert doubled.resources[1].components["output"] == disk.components["output"] * 2
    assert doubled.resources[0].estimated_bytes > expected.total
    assert disk.components["reference_indexes"] > 0


def test_shared_volume_is_compared_to_combined_temp_output_and_index_need(
    tmp_path, source, monkeypatch, caplog
):
    monkeypatch.setattr(resources, "_disk_space", lambda _: ("C:", 90 * GIB))
    estimate = resources.estimate_resources(
        "align",
        input_paths=[str(source)],
        output_dir=str(tmp_path / "output"),
        temp_dir=str(tmp_path / "spill"),
        reads=H2_READS,
        output_format="CRAM",
    )
    memory, disk, unknown = estimate.resources
    assert unknown.status == "UNKNOWN" and not unknown.paths
    assert disk.components["temporary"] < 90 * GIB
    assert disk.components["output"] < 90 * GIB
    assert disk.status == "LOW"
    with caplog.at_level(logging.INFO):
        resources.report_resources(estimate)
    assert "Preflight disk   LOW" in caplog.text
    assert str(tmp_path / "spill") in caplog.text
    assert memory.resource == "memory"


def test_separate_volumes_are_budgeted_independently(tmp_path, source, monkeypatch):
    out = str(tmp_path / "output")
    spill = str(tmp_path / "spill")
    monkeypatch.setattr(
        resources,
        "_disk_space",
        lambda path: ("C:", 20 * GIB) if path == out else ("E:", 80 * GIB),
    )
    estimate = resources.estimate_resources(
        "align",
        input_paths=[str(source)],
        output_dir=out,
        temp_dir=spill,
        reads=H2_READS,
        output_format="CRAM",
    )
    disks = [row for row in estimate.resources if row.resource == "disk" and row.paths]
    assert len(disks) == 2 and all(row.status == "OK" for row in disks)
    assert {tuple(row.components) for row in disks} == {
        ("output", "indexes"),
        ("temporary",),
    }


def test_gui_api_is_json_serializable_and_does_not_create_output_directories(
    source, tmp_path, monkeypatch
):
    asked = []
    monkeypatch.setattr(
        resources.shutil,
        "disk_usage",
        lambda path: asked.append(path) or SimpleNamespace(free=100 * GIB),
    )
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 2 * GIB)
    output = tmp_path / "missing" / "nested"
    estimate = resources.estimate_resources(
        "to-bam",
        input_paths=[str(source)],
        output_dir=str(output),
        threads="2",
    )
    encoded = json.loads(json.dumps(estimate.to_dict()))
    assert encoded["resources"][1]["components"]["output"] == 10_000
    assert encoded["resources"][0]["status"] == "OK"
    assert asked == [tmp_path.resolve(), tmp_path.resolve()]
    assert not output.exists()


def test_unavailable_measurements_are_unknown_not_false_ok(tmp_path, monkeypatch):
    def unreadable(_path):
        raise OSError("unavailable")

    monkeypatch.setattr(resources.shutil, "disk_usage", unreadable)
    estimate = resources.estimate_resources(
        "sort", input_paths=[str(tmp_path / "missing.cram")], output_dir=str(tmp_path)
    )
    assert estimate.resources[1].status == "UNKNOWN"
    assert estimate.resources[1].estimated_bytes is None
    assert estimate.resources[1].available_bytes is None


def test_unresolved_reference_has_a_structured_unknown_disk_row(tmp_path, source):
    estimate = resources.estimate_resources(
        "align", input_paths=[str(source)], output_dir=str(tmp_path), reads=100
    )
    unknown = [
        row for row in estimate.resources if row.resource == "disk" and not row.paths
    ]
    assert len(unknown) == 1
    assert unknown[0].status == "UNKNOWN"
    assert unknown[0].estimated_bytes is None
    assert unknown[0].available_bytes is None


@pytest.mark.parametrize("operation", ["align", "realign"])
def test_gatk_duplicate_marking_budgets_two_retained_bams(
    tmp_path, source, monkeypatch, operation
):
    reads = 1_000_000
    monkeypatch.setattr(resources, "_disk_space", lambda _: ("same", 180_000_000))
    estimate = resources.estimate_resources(
        operation,
        input_paths=[str(source)],
        output_dir=str(tmp_path),
        reads=reads,
        output_format="CRAM",
        samblaster=False,
        duplicate_marking="gatk-flow",
    )
    disk = estimate.resources[1]
    assert disk.components["temporary"] >= reads * resources.BAM_BYTES_PER_READ * 2
    assert disk.status == "LOW"


@pytest.mark.parametrize(
    "options", [{"threads": "bad"}, {"memory": "bad"}, {"reads": -1}]
)
def test_gui_estimator_remains_strict(tmp_path, source, options):
    with pytest.raises(ValueError):
        resources.estimate_resources(
            "sort", input_paths=[str(source)], output_dir=str(tmp_path), **options
        )


@needs_tools("samtools")
@pytest.mark.parametrize(
    "error",
    [
        ValueError("invalid estimate"),
        OSError("metadata unavailable"),
        subprocess.CalledProcessError(1, ["metadata"]),
    ],
)
def test_command_proceeds_when_advisory_estimation_fails(
    tmp_path, monkeypatch, caplog, error
):
    source, ref = _tiny_bam(tmp_path)

    def failed(*_a, **_k):
        raise error

    monkeypatch.setattr(bam, "estimate_resources", failed)
    outdir = tmp_path / "out"
    result = api.bam.to_cram(
        api.BamToCramParams(input=source, ref=ref, outdir=outdir, cram_version="3.1")
    )
    assert result.status == "ok"
    assert (outdir / "tiny.cram").stat().st_size > 0
    assert "resource estimate unavailable:" in caplog.text


def test_realign_includes_extracted_fastqs_only_when_needed(tmp_path, source):
    options = {
        "input_paths": [str(source)],
        "output_dir": str(tmp_path),
        "reads": 100_000,
        "output_format": "CRAM",
    }
    extract = resources.estimate_resources("realign", **options, extract_fastq=True)
    reuse = resources.estimate_resources("realign", **options)
    assert (
        extract.resources[1].estimated_bytes - reuse.resources[1].estimated_bytes
        == extract.resources[1].components["extracted_fastqs"]
    )
    assert extract.resources[0].estimated_bytes == reuse.resources[0].estimated_bytes


def test_disk_fallback_grows_with_input_bytes_for_the_same_reference(tmp_path, source):
    options = {"input_paths": [str(source)], "output_dir": str(tmp_path)}
    small = resources.estimate_resources("sort", **options)
    source.write_bytes(source.read_bytes() * 2)
    large = resources.estimate_resources("sort", **options)
    for component in ("temporary", "output"):
        assert (
            large.resources[1].components[component]
            == small.resources[1].components[component] * 2
        )
    assert large.resources[0].estimated_bytes == small.resources[0].estimated_bytes


def test_variant_worker_memory_and_regional_gatk_conversion(tmp_path, source):
    reference = tmp_path / "ref.fa"
    reference.write_text(">chr1\nA\n")
    Path(str(reference) + ".fai").write_text("chr1\t10\t0\t1\t2\nchr2\t10\t0\t1\t2\n")
    options = {
        "input_paths": [str(source)],
        "output_dir": str(tmp_path),
        "reference": str(reference),
        "threads": "6",
    }
    serial = resources.estimate_resources("snp", **options)
    full = resources.estimate_resources("snp", **options, parallel=True)
    region = resources.estimate_resources("indel", **options, region="chr1:1-10")
    assert full.resources[0].components["callers"] == 2 * GIB
    assert serial.resources[0].components["callers"] == GIB
    assert region.resources[0].components["callers"] == GIB
    gatk = resources.estimate_resources("gatk", **options, region="chr1:1-10")
    assert gatk.resources[1].components["regional_bam"] > 0
    assert any("whole input" in note for note in gatk.notes)


@pytest.fixture
def hg002_slice_metadata(tmp_path):
    source = tmp_path / "hg002.bam"
    # #722's measured BAM size; the read-only estimator needs only file metadata.
    sparse_truncate(source, 670_545_706)
    _, reference = _whole_genome(tmp_path)
    with Path(f"{reference}.fai").open("a") as stream:
        for number in range(430):
            stream.write(f"alt{number}\t1000\t0\t60\t61\n")
    return source, reference


def test_722_extracted_fastq_budget_covers_full_quality_hg002_slice(
    tmp_path, hg002_slice_metadata
):
    source, reference = hg002_slice_metadata
    estimate = resources.estimate_resources(
        "realign",
        input_paths=[str(source)],
        output_dir=str(tmp_path),
        reference=str(reference),
        reads=10_971_483,
        extract_fastq=True,
    )
    disk = next(row for row in estimate.resources if row.resource == "disk")
    # Actual completed R1/R2 outputs retained by the #722 SIGINT run.
    assert disk.components["extracted_fastqs"] >= 460_014_704 + 477_371_236


@pytest.mark.parametrize("operation", ["snp", "indel"])
def test_722_bcftools_preflight_does_not_falsely_report_small_slice_disk_low(
    tmp_path, monkeypatch, hg002_slice_metadata, operation
):
    source, reference = hg002_slice_metadata
    monkeypatch.setattr(
        resources.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=64 * 1024**2),
    )
    estimate = resources.estimate_resources(
        operation,
        input_paths=[str(source)],
        output_dir=str(tmp_path / "out"),
        reference=str(reference),
        threads="4",
        parallel=operation == "indel",
        available_ram=14 * GIB,
    )
    disk = next(row for row in estimate.resources if row.resource == "disk")
    # Serial SNPs and non-replaying indels fit; parallel SNPs also reserve replay.
    assert disk.status == "OK"


@pytest.mark.parametrize("operation", ["snp", "indel"])
@pytest.mark.parametrize(
    "options",
    [
        {"parallel": False, "threads": "4"},
        {"parallel": True, "threads": "1"},
        {"parallel": True, "threads": "4", "region": "chr1:1-10"},
        {"parallel": True, "threads": "4", "bcftools_contigs": {}},
    ],
)
def test_729_serial_calling_does_not_charge_nonexistent_parts(
    tmp_path, monkeypatch, hg002_slice_metadata, operation, options
):
    source, reference = hg002_slice_metadata
    monkeypatch.setattr(
        resources.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=16 * 1024**2),
    )
    estimate = resources.estimate_resources(
        operation,
        input_paths=[str(source)],
        output_dir=str(tmp_path / "out"),
        reference=str(reference),
        temp_dir=str(tmp_path / "unused"),
        **options,
    )
    disk = next(row for row in estimate.resources if row.resource == "disk")
    assert disk.status == "OK"
    assert "temporary" not in disk.components
    assert str(tmp_path / "unused") not in disk.paths


def test_729_unresolved_parallel_plan_does_not_claim_serial_disk_usage(
    tmp_path, source
):
    estimate = resources.estimate_resources(
        "snp",
        input_paths=[str(source)],
        output_dir=str(tmp_path),
        threads="4",
        parallel=True,
    )
    disk = next(row for row in estimate.resources if row.resource == "disk")
    assert disk.status == "UNKNOWN"


@pytest.fixture
def batched_header_sample(tmp_path):
    rng = random.Random(729)
    names = [f"chrSmall{i:04d}_" + "x" * 48 for i in range(600)]
    sequences = {"chrBig": "ACGT" * 25_000}
    sequences.update(
        {
            name: "".join(rng.choice("ACGT") for _ in range(1000)) if i < 2 else "A"
            for i, name in enumerate(names)
        }
    )
    reference = write_fasta(tmp_path / "header.fa", sequences)
    lines = ["@HD\tVN:1.6\tSO:coordinate"]
    lines += [f"@SQ\tSN:{name}\tLN:{len(seq)}" for name, seq in sequences.items()]
    lines += ["@RG\tID:r\tSM:sample"]
    for i, (name, seq) in enumerate(sequences.items()):
        if name in names[:2]:
            for start in range(1, 701):
                bases = list(seq[start - 1 : start + 299])
                if 201 <= start <= 500 and rng.random() < 0.5:
                    bases[500 - start] = "ACGT"[("ACGT".index(seq[499]) + 1) % 4]
                quality = "".join(chr(33 + rng.randint(15, 40)) for _ in bases)
                lines.append(
                    f"r{i}_{start}\t0\t{name}\t{start}\t60\t300M\t*\t0\t0\t"
                    f"{''.join(bases)}\t{quality}\tRG:Z:r"
                )
        else:
            bases = seq[:100]
            lines.append(
                f"r{i}\t0\t{name}\t1\t60\t{len(bases)}M\t*\t0\t0\t"
                f"{bases}\t{'I' * len(bases)}\tRG:Z:r"
            )
    return str(sam_to_bam(lines, tmp_path / "header.bam")), str(reference)


@needs_tools("samtools", "bcftools")
@pytest.mark.parametrize("operation", ["snp", "indel"])
def test_729_batched_headers_cover_real_deep_replay_without_overcharging_indels(
    tmp_path, monkeypatch, batched_header_sample, operation
):
    alignment, reference = batched_header_sample
    selected = contig_calling.calling_contigs(alignment, reference, "2", None, True)
    options = {
        "input_paths": [alignment],
        "output_dir": str(tmp_path),
        "reference": reference,
        "threads": "2",
        "parallel": True,
        "bcftools_contigs": selected,
        "alignment_header": get_bam_header(alignment),
    }
    estimate = resources.estimate_resources(operation, **options)
    disk = next(row for row in estimate.resources if row.resource == "disk")
    original = contig_calling.tempfile.TemporaryDirectory
    observed = {}

    @contextmanager
    def measure_work(*args, **kwargs):
        with original(*args, **kwargs) as work:
            yield work
            if Path(work).name.startswith(".wgse_call_"):
                files = [path for path in Path(work).rglob("*") if path.is_file()]
                observed["bytes"] = sum(path.stat().st_size for path in files)
                observed["raw_parts"] = any(path.suffix == ".vcf" for path in files)

    # OS boundary: inspect real files immediately before temporary cleanup.
    monkeypatch.setattr(contig_calling.tempfile, "TemporaryDirectory", measure_work)
    output = str(tmp_path / f"{operation}.vcf.gz")
    contig_calling.call_small_variants(
        alignment,
        reference,
        output,
        threads="2",
        region=None,
        profile=[],
        call_args=["--ploidy", "2", "-P", "0"],
        contigs=selected,
        indels=operation == "indel",
    )
    assert Path(output).is_file()
    assert disk.components["temporary"] >= observed["bytes"]
    assert observed["raw_parts"] == (operation == "snp")
    if operation == "indel":
        monkeypatch.setattr(
            resources.shutil,
            "disk_usage",
            lambda _path: SimpleNamespace(free=8 * 1024**2),
        )
        checked = resources.estimate_resources(operation, **options)
        assert (
            next(row for row in checked.resources if row.resource == "disk").status
            == "OK"
        )


@pytest.mark.parametrize("operation", ["snp", "indel"])
def test_722_vcf_budget_with_known_read_count_is_independent_of_bam_cram_storage(
    tmp_path, hg002_slice_metadata, operation
):
    source, reference = hg002_slice_metadata
    cram = tmp_path / "hg002.cram"
    sparse_truncate(cram, 100_000_000)
    budgets = []
    for alignment in (source, cram):
        estimate = resources.estimate_resources(
            operation,
            input_paths=[str(alignment)],
            output_dir=str(tmp_path),
            reference=str(reference),
            reads=11_140_364,
        )
        disk = next(row for row in estimate.resources if row.resource == "disk")
        budgets.append(disk.components["output"])
    assert budgets[0] == budgets[1]


class Reported(Exception):
    pass


@pytest.mark.parametrize(
    ("module", "command", "operation"),
    [
        pytest.param(bam, bam.cmd_sort, "sort", marks=needs_tools("samtools")),
        pytest.param(bam, bam.cmd_tocram, "to-cram", marks=needs_tools("samtools")),
        pytest.param(bam, bam.cmd_tobam, "to-bam", marks=needs_tools("samtools")),
        pytest.param(basic, basic.cmd_snp, "snp", marks=needs_tools("samtools")),
        pytest.param(basic, basic.cmd_indel, "indel", marks=needs_tools("samtools")),
        (callers, callers.cmd_gatk, "gatk"),
    ],
)
def test_commands_report_before_work(
    module, command, operation, tmp_path, source, monkeypatch
):
    monkeypatch.setattr(module, "verify_dependencies", lambda *_: None)
    if operation == "gatk":
        monkeypatch.setattr(module, "require_native_tool", lambda _tool: None)
    monkeypatch.setattr(module, "log_dependency_info", lambda *_: None, raising=False)
    ref = None
    if module in (bam, basic):
        source, ref = _tiny_bam(tmp_path)
    if module is not bam:
        setup = basic.CallerSetup(
            "1", str(tmp_path), "", ref if module is basic else None, None
        )  # type: ignore[arg-type]
        monkeypatch.setattr(module, "setup_caller", lambda _params: setup)

    def reported(build):
        estimate = build()
        assert estimate.operation == operation
        raise Reported

    monkeypatch.setattr(module, "preflight_resources", reported)
    outdir = tmp_path / "out"
    outdir.mkdir()
    args = argparse.Namespace(
        input=str(source), region=None, gpu=False, outdir=str(outdir)
    )
    with pytest.raises(Reported):
        command(args)


@pytest.mark.parametrize("operation", ["align", "realign"])
@pytest.mark.parametrize("platform", ["illumina", "ultima"])
def test_alignment_reports_before_index_building_or_fastq_extraction(
    operation, platform, tmp_path, source, monkeypatch
):
    monkeypatch.setattr(host_platform, "machine", lambda: "x86_64")
    ref = tmp_path / "ref.fa"
    ref.write_text(">chr1\nA\n")

    monkeypatch.setattr(
        align, "get_tool_path", lambda name: "gatk" if name == "gatk" else None
    )

    def reported(build):
        estimate = build()
        assert estimate.operation == operation
        if operation == "realign" and platform != "ultima":
            assert "extracted_fastqs" not in estimate.resources[1].components
            assert "collation_spill" not in estimate.resources[1].components
            assert estimate.resources[1].components["temporary"] > 0
            assert estimate.resources[0].components["collation"] > 0
        if platform == "ultima":
            assert estimate.resources[1].components["temporary"] == 20_000
        raise Reported

    if operation == "align":
        monkeypatch.setattr(
            align,
            "_prepare_alignment",
            lambda *_a, **_k: align._AlignSetup(
                "1", "256M", str(tmp_path), str(ref), "sample", "r1.fq"
            ),
        )
        monkeypatch.setattr(
            align,
            "fastq_input_statistics",
            lambda *_: SimpleNamespace(reads=100, read_length=150),
        )
        monkeypatch.setattr(align, "preflight_resources", reported)
        monkeypatch.setattr(
            align, "ensure_bwa_index", lambda *_: pytest.fail("indexing started")
        )
        with pytest.raises(Reported):
            align._bwa_setup(
                align.AlignJob(
                    r1="r1.fq", aligner="bwa", platform=platform, format="CRAM"
                ),
                "align",
            )
    else:
        monkeypatch.setattr(align, "streaming_mapping_fits", lambda *_a, **_kw: True)
        monkeypatch.setattr(realign, "resolve_reference", lambda *_: str(ref))
        monkeypatch.setattr(realign, "indexed_read_count", lambda *_: 100)
        monkeypatch.setattr(realign, "_input_cram_reference", lambda *_: [])
        monkeypatch.setattr(realign, "preflight_resources", reported)
        monkeypatch.setattr(
            realign,
            "alignments_to_fastq",
            lambda *_a, **_k: pytest.fail("extraction started"),
        )
        with pytest.raises(Reported):
            run_wgsextract(
                "realign",
                "--input",
                str(source),
                "--ref",
                str(ref),
                "--aligner",
                "bwa",
                "--platform",
                platform,
                "--format",
                "CRAM",
                "--threads",
                "1",
                "--no-preflight",
            )


def test_long_read_disk_reads_compression_from_the_file_and_counts_fastqs(tmp_path):
    """#484 review: a gzipped FASTQ named reads.fq was sized as plain text, and
    a long-read realign's extracted FASTQs made the disk estimate unknown."""
    rng = random.Random(484)
    fastq = "".join(
        f"@r{i}\n{''.join(rng.choices('ACGT', k=5000))}\n+\n{'5' * 5000}\n"
        for i in range(50)
    )
    disks = []
    for name in ("reads.fq.gz", "reads.fq"):
        path = tmp_path / name
        path.write_bytes(gzip.compress(fastq.encode()))
        estimate = resources.estimate_resources(
            "align", input_paths=[str(path)], output_dir=str(tmp_path),
            aligner="minimap2", preset="map-ont", output_format="CRAM",
        )  # fmt: skip
        [disk] = [disk for disk in estimate.resources[1:] if disk.paths]
        disks.append(disk.estimated_bytes)
    assert disks[0] is not None and disks[0] == disks[1]

    cram = tmp_path / "ont.cram"
    cram.write_bytes(b"\0" * 10**6)
    estimate = resources.estimate_resources(
        "realign", input_paths=[str(cram)], output_dir=str(tmp_path),
        aligner="minimap2", preset="map-ont", extract_fastq=True,
    )  # fmt: skip
    [disk] = [disk for disk in estimate.resources[1:] if disk.paths]
    assert disk.estimated_bytes is not None
    assert 10**6 < disk.components["extracted_fastqs"] < 3 * 10**6


def test_long_read_disk_covers_the_measured_spills_and_cram_output():
    """#474: the long-read CRAM -> CRAM realign disk budget must not fall below
    what samtools sort and the CRAM writer were measured to use."""
    mib = 1024**2
    # measured: out/474-475/spill-hs38/result.txt, HG002 ONT chr20 slice onto
    # hs38, samtools sort -@1 -m 8M: 70,339,983 B of CRAM in, a 310,407,552 B
    # spill peak and 133,758,318 B of CRAM out.
    small = resources.long_read_disk("chr20.cram", 70_339_983, "CRAM", 8 * mib)
    assert small.spill >= 310_407_552
    # measured: out/474-475/supplementary_inflation.txt, minimap2 -Y adds 1.91x
    # the bases as supplementary records in that slice but at most 1.70x in the
    # genome regions sampled; the CRAM output grows with them.
    assert small.output >= 133_758_318 * 1.70 / 1.91

    # measured: out/474-475/attempt2_spill_progress.txt, #431 attempt 2
    # (--threads 4 --memory 192M, so a 4 x 192 MiB sort buffer): 42,570,898,460
    # B of spills mid-merge once 10,728,434,251 B of the CRAM had been read.
    genome = resources.long_read_disk(
        "PAO83395.pass.cram", 10_728_434_251, "CRAM", 4 * 192 * mib
    )
    assert genome.spill > 42_570_898_460
