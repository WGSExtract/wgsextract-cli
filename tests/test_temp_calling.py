"""Calling preflight and publication follow the volume holding their work."""

import errno
import logging
import os
from pathlib import Path

import pytest

from tests.disk_volumes import GIB, Volumes
from tests.htslib_helpers import (
    needs_tools,
    sam_to_bam,
    snp_sample,
    vcf_calls,
    write_fasta,
)
from tests.test_clair3 import DORADO_RG, as_cram, make_model
from tests.test_clair3 import fake_clair3 as fake_clair3
from tests.test_clair3 import linux_x86 as linux_x86
from tests.test_gatk_scatter import gatk as gatk
from tests.test_parabricks import gpu as gpu
from tests.test_parabricks import linux as linux
from tests.test_parabricks import posix_only
from wgsextract_cli import api
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.resource_estimates import estimate_resources


@needs_tools("samtools", "bcftools", "tabix")
@pytest.mark.parametrize("short", [None, "output", "scratch"])
def test_1093_gatk_evidence_work_and_published_gvcf_use_their_actual_volumes(
    tmp_path, monkeypatch, caplog, request, short
):
    sample = snp_sample(tmp_path)
    output, scratch, java = (tmp_path / name for name in ("out", "scratch", "java"))
    for folder in (output, scratch, java):
        folder.mkdir()
    volumes = Volumes(monkeypatch)
    volumes.free[output] = 0 if short == "output" else 0.04
    volumes.free[scratch] = 0 if short == "scratch" else 0.08
    volumes.free[java] = 0.01

    def estimate(staging):
        return estimate_resources(
            "gatk", input_paths=[sample.bam], output_dir=str(output),
            reference=sample.ref, reads=10_971_483, reference_evidence=True,
            temp_dir=str(java), gatk_staging_dir=staging,
        )  # fmt: skip

    rows = {
        row.paths[0]: row
        for row in estimate(str(scratch)).resources
        if row.resource == "disk"
    }
    assert rows[str(output)].status == ("LOW" if short == "output" else "OK")
    assert rows[str(scratch)].status == ("LOW" if short == "scratch" else "OK")
    assert (
        rows[str(scratch)].components["reference_evidence"]
        > rows[str(output)].components["reference_evidence"]
    )
    assert "reference_evidence" not in rows[str(java)].components
    if short is not None:
        return
    fallback = next(row for row in estimate(None).resources if str(output) in row.paths)
    assert fallback.components["reference_evidence"] == (
        rows[str(output)].components["reference_evidence"]
        + rows[str(scratch)].components["reference_evidence"]
    )
    request.getfixturevalue("gatk")
    monkeypatch.setitem(settings, "temp_directory", str(scratch))
    caplog.set_level(logging.INFO)
    result = api.vcf.gatk(
        api.VcfGatkParams(
            input=sample.bam, ref=sample.ref, outdir=output, threads=1,
            region="chr1:1-3000", reference_evidence="gvcf",
        )
    )  # fmt: skip
    assert result.status == "ok"
    assert (output / "gatk.g.vcf.gz").is_file()
    reports = [
        record.message
        for record in caplog.records
        if record.message.startswith("Preflight disk")
    ]
    assert any(str(scratch) in message for message in reports)
    assert list(scratch.iterdir()) == []


@needs_tools("samtools", "bcftools", "tabix")
@pytest.mark.parametrize("operation", ["snp", "indel"])
@pytest.mark.parametrize("short", [None, "scratch", "output"])
def test_1093_bcftools_reports_parts_on_the_temporary_volume(
    tmp_path, monkeypatch, caplog, operation, short
):
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "A" * 1000, "chr2": "A" * 1000})
    source = sam_to_bam(
        ["@SQ\tSN:chr1\tLN:1000", "@SQ\tSN:chr2\tLN:1000", "@RG\tID:r\tSM:S"]
        + [
            f"{chrom}_{n}\t0\t{chrom}\t1\t60\t100M\t*\t0\t0\t"
            f"{'G' * 100}\t{'I' * 100}\tRG:Z:r"
            for chrom in ("chr1", "chr2")
            for n in range(20)
        ],
        tmp_path / "sample.bam",
    )
    output, scratch = tmp_path / "out", tmp_path / "scratch"
    output.mkdir()
    scratch.mkdir()
    volumes = Volumes(monkeypatch)
    volumes.free[output] = 0 if short == "output" else 1
    volumes.free[scratch] = 0 if short == "scratch" else 1
    estimate = estimate_resources(
        operation, input_paths=[source], output_dir=str(output), temp_dir=str(scratch),
        reference=str(ref), reads=100_000, parallel=True, threads="2",
        bcftools_contigs={"chr1": 1000, "chr2": 1000}, reference_evidence=True,
    )  # fmt: skip
    disks = {row.paths[0]: row for row in estimate.resources if row.resource == "disk"}
    assert disks[str(output)].status == ("LOW" if short == "output" else "OK")
    assert disks[str(scratch)].status == ("LOW" if short == "scratch" else "OK")
    assert {"temporary", "reference_evidence_parts"} <= disks[
        str(scratch)
    ].components.keys()
    assert (
        not {"temporary", "reference_evidence_parts"}
        & disks[str(output)].components.keys()
    )
    if short is not None:
        return
    monkeypatch.setitem(settings, "temp_directory", str(scratch))
    caplog.set_level(logging.INFO)
    params = api.VcfSnpParams if operation == "snp" else api.VcfIndelParams
    command = api.vcf.snp if operation == "snp" else api.vcf.indel
    result = command(
        params(
            input=source, ref=ref, outdir=output, threads=2,
            parallel=True, reference_evidence="gvcf", ploidy="2",
        )
    )  # fmt: skip
    assert any(Path(o.path).suffix == ".gz" for o in result.outputs)
    disk_reports = [
        record.message
        for record in caplog.records
        if record.message.startswith("Preflight disk")
    ]
    assert any(str(scratch) in message for message in disk_reports)
    assert list(scratch.iterdir()) == []


@needs_tools("samtools", "bcftools", "tabix")
@pytest.mark.skipif(
    os.name == "nt",
    reason="Native Clair3 is unsupported on Windows; this POSIX staging fixture mocks a Linux host.",
)
@pytest.mark.parametrize("fake_clair3", ["2.0.3"], indirect=True)
@pytest.mark.parametrize("short_scratch", [False, True])
def test_1093_clair3_checks_tensors_and_staged_cram_on_the_tmp_volume(
    tmp_path, monkeypatch, linux_x86, fake_clair3, ram, short_scratch
):
    ram(64 * GIB)
    sample = snp_sample(tmp_path, DORADO_RG)
    cram = as_cram(sample, "3.1", tmp_path / "sample.cram")
    model = make_model(tmp_path, "my_r1041_model")
    output, scratch = tmp_path / "out", tmp_path / "scratch"
    output.mkdir()
    scratch.mkdir()
    volumes = Volumes(monkeypatch)
    volumes.free[output] = 1 / 1024  # Final VCF fits, tensors do not.
    volumes.free[scratch] = 0 if short_scratch else 16
    monkeypatch.setitem(settings, "temp_directory", str(scratch))
    params = api.VcfClair3Params(
        input=cram, ref=sample.ref, outdir=output, model=model, threads=1
    )
    if short_scratch:
        with pytest.raises(api.CommandFailed, match=str(scratch)):
            api.vcf.clair3(params)
        assert not (output / "clair3.vcf.gz").exists()
    else:
        api.vcf.clair3(params)
        assert vcf_calls(output / "clair3.vcf.gz") == sample.calls
    assert list(scratch.iterdir()) == []


@posix_only
@needs_tools("samtools", "bcftools", "tabix")
def test_1093_diploid_parabricks_gvcf_publishes_across_volumes(
    tmp_path, monkeypatch, gpu
):
    sample = snp_sample(tmp_path)
    output, scratch = tmp_path / "out", tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setitem(settings, "temp_directory", str(scratch))
    real_replace = os.replace

    def cross_volume(source, destination):
        if Path(source).is_relative_to(scratch) and Path(destination).is_relative_to(
            output
        ):
            raise OSError(errno.EXDEV, "separate temporary volume")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", cross_volume)
    result = api.vcf.deepvariant(
        api.VcfDeepvariantParams(
            input=sample.bam, ref=sample.ref, outdir=output, gpu=True, threads=1,
            region="chr1:1000-1100", reference_evidence="gvcf",
        )
    )  # fmt: skip
    files = {o.role: o.path for o in result.outputs}
    assert [(c[0], c[1]) for c in vcf_calls(files["variants"])] == [("chr1", "1000")]
    assert (output / "deepvariant.g.vcf.gz").is_file()
    assert (output / "deepvariant.g.vcf.gz.tbi").is_file()
    assert list(scratch.iterdir()) == []
