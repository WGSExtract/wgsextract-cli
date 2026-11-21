"""Ensembl VEP's local cache boundary (#1238)."""

import sys
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, vcf_calls
from tests.test_vep_run import _bam_with_snps, _vcf, ensembl_test_cache
from tests.test_vep_run import fake_vep as fake_vep
from wgsextract_cli import api
from wgsextract_cli.api import VepRunParams

pytestmark = [
    needs_htslib,
    pytest.mark.skipif(sys.platform == "win32", reason="Ensembl VEP needs POSIX"),
]


@pytest.mark.parametrize("refseq", [False, True])
def test_1238_ensembl_raw_release_override_still_uses_local_cache(
    tmp_path, fake_vep, refseq
):
    vcf = _vcf(tmp_path / "s.vcf.gz")
    cache = ensembl_test_cache(tmp_path / "cache", version="115")
    if refseq:
        (cache / "homo_sapiens").rename(cache / "homo_sapiens_refseq")
    extra = " --refseq" if refseq else ""

    run_wgsextract(
        "vep", "run", "-i", vcf, "--vep-cache", str(cache), "--assembly", "GRCh38",
        f"--vep-args=--cache_version 115{extra}",
    )  # fmt: skip

    [args] = fake_vep()
    # contract: annotation must not send sample-derived queries to Ensembl.
    assert "--offline" in args and "--database" not in args
    assert vcf_calls(tmp_path / "wgse" / "s_vep.vcf.gz") == [
        ("1", "10", "A", "G", "0/1")
    ]


def test_1238_missing_ensembl_cache_stops_before_bam_variant_calling(
    tmp_path, fake_vep, monkeypatch, caplog
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    fasta, bam = _bam_with_snps(tmp_path)
    params = VepRunParams(
        input=Path(bam), ref=Path(fasta), engine="ensembl", vep_assembly="GRCh38",
        vep_cache=tmp_path / "missing",
    )  # fmt: skip

    with caplog.at_level("INFO"), pytest.raises(api.CommandFailed) as caught:
        api.vep.run(params)
    error = caught.value.result.error
    assert error is not None and error.code == "vep_cache_missing"
    assert "Performing variant calling" not in caplog.text
    assert not fake_vep()


def test_1238_unknown_assembly_guidance_does_not_default_to_grch38(
    tmp_path, fake_vep, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    vcf = Path(_vcf(tmp_path / "s.vcf.gz"))
    index = Path(f"{vcf}.tbi")
    index.unlink()
    with pytest.raises(api.CommandFailed) as caught:
        api.vep.run(
            VepRunParams(input=vcf, engine="ensembl", vep_cache=tmp_path / "missing")
        )
    error = caught.value.result.error
    assert error is not None and error.code == "vep_cache_missing"
    assert "Could not infer the assembly" in error.message
    assert "use the same --assembly for download and annotation" in error.message
    assert "--assembly ASSEMBLY" in error.message
    assert not index.exists()
    assert not fake_vep()
