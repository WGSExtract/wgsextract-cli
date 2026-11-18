"""A failed rerun must preserve a readable VCF and the index for those bytes."""

import errno
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, snp_sample
from tests.test_parabricks import gpu as gpu
from tests.test_parabricks import linux as linux
from tests.test_vep_run import _vcf
from tests.test_vep_run import fake_vep as fake_vep
from wgsextract_cli import api
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.utils import run_command

pytestmark = [
    needs_tools("samtools", "bcftools", "bgzip", "tabix"),
    pytest.mark.skipif(
        os.name == "nt", reason="Ensembl VEP and Parabricks fixtures require POSIX"
    ),
]


@pytest.mark.parametrize("engine", ["vep", "parabricks"])
@pytest.mark.parametrize("component", ["vcf", "index"])
@pytest.mark.parametrize("interrupted", [False, True], ids=["disk-full", "interrupted"])
def test_1093_failed_cross_volume_rerun_preserves_the_previous_indexed_vcf(
    tmp_path, monkeypatch, request, engine, component, interrupted
):
    scratch, output = tmp_path / "scratch", tmp_path / "out"
    scratch.mkdir()
    monkeypatch.setitem(settings, "temp_directory", str(scratch))
    if engine == "vep":
        request.getfixturevalue("fake_vep")
        source = Path(_vcf(tmp_path / "sample.vcf.gz"))
        cache = tmp_path / "cache"
        cache.mkdir()
        params = api.VepRunParams(
            input=source, outdir=output, vep_cache=cache, vep_assembly="GRCh38",
            engine="ensembl", force=True, threads=1,
        )  # fmt: skip
        command = api.vep.run
        final = output / "sample_vep.vcf.gz"
        region = "1:1-100"
    else:
        gpu_host = request.getfixturevalue("gpu")
        # Model pbrun's sibling tabix index with the real indexer.
        with (gpu_host.bin.parent / "image" / "pbrun").open("a") as script:
            script.write(
                '\nif tool == "deepvariant":\n'
                '    subprocess.run([TOOLS["bcftools"], "index", "-t", vcf], check=True)\n'
            )
        sample = snp_sample(tmp_path)
        params = api.VcfDeepvariantParams(
            input=sample.bam, ref=sample.ref, outdir=output, gpu=True, threads=1,
            region="chr1:1000-1100", reference_evidence="gvcf",
        )  # fmt: skip
        command = api.vcf.deepvariant
        final = output / "deepvariant.vcf.gz"
        region = "chr1:1000-1100"
    command(params)
    index = Path(str(final) + ".tbi")
    before = final.read_bytes(), index.read_bytes()
    expected = run_command(
        ["bcftools", "query", "-r", region, "-f", "%CHROM:%POS:%ALT\n", final],
        capture_output=True,
    ).stdout
    assert expected.strip()
    if engine == "vep":
        _vcf(source, "2\t900\t.\tA\tT\t50\tPASS\t.\tGT\t0/1\n")
    else:
        params = replace(params, region="chr1:2000-2100")
    real_replace, real_copy = os.replace, shutil.copy2

    def cross_volume(source_path, destination):
        if Path(source_path).is_relative_to(scratch) and not Path(
            destination
        ).is_relative_to(scratch):
            raise OSError(errno.EXDEV, "separate temporary volume")
        real_replace(source_path, destination)

    def failed_copy(source_path, destination, **kwargs):
        path = Path(source_path)
        selected = path.suffix == (".tbi" if component == "index" else ".gz")
        if path.is_relative_to(scratch) and selected:
            Path(destination).write_bytes(b"partial")
            if interrupted:
                raise KeyboardInterrupt
            raise OSError(errno.ENOSPC, "disk full")
        return real_copy(source_path, destination, **kwargs)

    monkeypatch.setattr(os, "replace", cross_volume)
    monkeypatch.setattr(shutil, "copy2", failed_copy)
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            command(params, check=False)
    else:
        result = command(params, check=False)
        assert result.status == "failed"
    assert (final.read_bytes(), index.read_bytes()) == before
    assert (
        run_command(
            ["bcftools", "query", "-r", region, "-f", "%CHROM:%POS:%ALT\n", final],
            capture_output=True,
        ).stdout
        == expected
    )
    assert not list(output.glob("*.partial*"))
    assert not [p for p in output.iterdir() if p.name.startswith(".")]
    assert list(scratch.iterdir()) == []
