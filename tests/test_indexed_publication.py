"""A failed rerun must preserve a readable VCF and the index for those bytes."""

import errno
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, snp_sample, vcf_body
from tests.test_parabricks import gpu as gpu
from tests.test_parabricks import parabricks_x86 as parabricks_x86
from tests.test_vep_run import _vcf, ensembl_test_cache
from tests.test_vep_run import fake_vep as fake_vep
from wgsextract_cli import api
from wgsextract_cli.core.atomic_output import output_publication_scope
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.vcf_output import publish_called_vcf

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
        cache = ensembl_test_cache(tmp_path / "cache")
        params = api.VepRunParams(
            input=source, outdir=output, vep_cache=cache, vep_assembly="GRCh38",
            engine="ensembl", force=True, threads=1,
        )  # fmt: skip
        command = api.vep.run
        final = output / "sample_vep.vcf.gz"
        region = "1:1-100"
    else:
        request.getfixturevalue("gpu")
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
    files = [final, index]
    if engine == "parabricks":
        files += [output / "deepvariant.g.vcf.gz", output / "deepvariant.g.vcf.gz.tbi"]
    before = [file.read_bytes() for file in files]
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
    selected_output = index if component == "index" else final
    injected = False

    def fail():
        nonlocal injected
        injected = True
        if interrupted:
            raise KeyboardInterrupt
        raise OSError(errno.ENOSPC, "disk full")

    def cross_volume(source_path, destination):
        source_path, destination = Path(source_path), Path(destination)
        if source_path.is_relative_to(scratch) and not destination.is_relative_to(
            scratch
        ):
            raise OSError(errno.EXDEV, "separate temporary volume")
        # Parabricks recompresses into a private destination-local directory.
        # Fail its final rename, after the old callset has been backed up.
        if (
            engine == "parabricks"
            and destination == selected_output
            and source_path.name == destination.name
        ):
            fail()
        real_replace(source_path, destination)

    def failed_copy(source_path, destination, **kwargs):
        path = Path(source_path)
        selected = path.suffix == (".tbi" if component == "index" else ".gz")
        if path.is_relative_to(scratch) and selected:
            Path(destination).write_bytes(b"partial")
            fail()
        return real_copy(source_path, destination, **kwargs)

    monkeypatch.setattr(os, "replace", cross_volume)
    if engine == "vep":
        monkeypatch.setattr(shutil, "copy2", failed_copy)
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            command(params, check=False)
    else:
        result = command(params, check=False)
        assert result.status == "failed"
    assert injected
    assert [file.read_bytes() for file in files] == before
    assert (
        run_command(
            ["bcftools", "query", "-r", region, "-f", "%CHROM:%POS:%ALT\n", final],
            capture_output=True,
        ).stdout
        == expected
    )
    assert not list(output.glob("*.partial*"))
    assert not [
        path
        for path in output.iterdir()
        if path.name.startswith(".") and not path.match(".wgse_publish-*.guard")
    ]
    assert list(scratch.iterdir()) == []


def _lowercase_vcf(path):
    path.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=100>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\t.\ta\tc\t.\tPASS\t.\n",
        newline="\n",
    )
    return path


def test_1654_caller_publication_lock_lasts_until_invocation_exits(tmp_path):
    import fcntl

    source = _lowercase_vcf(tmp_path / "input.vcf")
    final = tmp_path / "calls.vcf.gz"
    with output_publication_scope():
        publish_called_vcf(str(final), source=str(source))
        (guard,) = tmp_path.glob(".wgse_publish-*.guard")
        with guard.open("rb") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with guard.open("rb") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_1654_hardlinked_vcf_sibling_keeps_its_shared_index(tmp_path):
    source = _lowercase_vcf(tmp_path / "input.vcf")
    final = tmp_path / "calls.vcf.gz"
    with final.open("wb") as stream:
        run_command(["bgzip", "-c", source], stdout=stream)
    sibling = final.with_suffix(".bgz")
    os.link(final, sibling)
    run_command(["tabix", "-C", "-p", "vcf", final])
    short = Path(f"{final}.csi").rename(final.with_suffix(".csi"))
    before = short.read_bytes()

    with pytest.raises(WGSExtractError, match=r"calls\.vcf\.bgz shares .*\.csi"):
        publish_called_vcf(str(final), source=str(source))

    assert short.read_bytes() == before
    query = run_command(["tabix", sibling, "chr1:10-10"], capture_output=True)
    assert query.stdout.encode() == vcf_body(final)
    assert "\ta\tc\t" in query.stdout
