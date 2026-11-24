"""`vep run` assembly and FASTA choice."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_vep_run import _value, ensembl_test_cache
from wgsextract_cli.api import VepRunParams


def _command(lib, **overrides):
    from wgsextract_cli.commands._vep_run import _build_vep_command, _vep_assembly

    fields = {
        "input": Path("in.vcf.gz"),
        "engine": "ensembl",
    }
    params = VepRunParams(**{**fields, **overrides})
    return _build_vep_command(
        params, "in.vcf.gz", "out.vcf", "snp-indel", lib, "2",
        assembly=_vep_assembly(params, lib, "in.vcf.gz"),
    )  # fmt: skip


@pytest.mark.parametrize(
    ("build", "assembly"), [("hg38", "GRCh38"), ("hg19", "GRCh37"), (None, None)]
)
def test_assembly_follows_the_library_build(tmp_path, build, assembly):
    ensembl_test_cache(tmp_path, assembly or "GRCh38")
    cmd = _command(
        SimpleNamespace(build=build, fasta="/r/g.fa", vep_cache=str(tmp_path))
    )
    assert _value(cmd, "--fasta") == "/r/g.fa"
    if assembly:
        assert _value(cmd, "--assembly") == assembly
    else:
        assert "--assembly" not in cmd


def test_library_cache_used_when_no_cache_option(tmp_path):
    ensembl_test_cache(tmp_path)
    lib = SimpleNamespace(build="hg38", fasta=None, vep_cache=str(tmp_path))
    cmd = _command(lib, vep_cache=None)
    assert _value(cmd, "--dir_cache") == str(tmp_path)
    assert "--offline" in cmd and "--fasta" not in cmd


def test_vep_fast_preset_replaces_everything(tmp_path):
    ensembl_test_cache(tmp_path)
    lib = SimpleNamespace(build="hg38", fasta=None, vep_cache=str(tmp_path))

    assert "--everything" in _command(lib, preset="everything")
    fast = _command(lib, preset="fast")
    assert "--everything" not in fast
    assert {"--symbol", "--mane", "--sift", "--polyphen"} <= set(fast)
    assert "--af_gnomadg" not in fast and "--hgvs" not in fast


@pytest.mark.parametrize(
    ("fasta", "forced", "kept"),
    [
        ("/lib/hg38.fa", "GRCh37", False),
        ("/lib/hs37d5.fa", "GRCh38", False),
        ("/lib/GRCh38.fa", "GRCh38", True),
        ("/lib/genome.fa", "GRCh37", True),
    ],
)
def test_forced_assembly_drops_a_conflicting_fasta(fasta, forced, kept):
    from wgsextract_cli.commands._vep_run import _apply_forced_assembly
    from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary

    lib = ReferenceLibrary(None)
    lib.fasta = fasta
    _apply_forced_assembly(lib, forced)
    assert lib.build == ("hg38" if forced == "GRCh38" else "hg19")
    assert lib.fasta == (fasta if kept else None)


@pytest.mark.parametrize("name", ["GRCh38.fa", "custom.fa"])
def test_issue_1004_forced_assembly_keeps_fasta_under_misleading_parent(name):
    from wgsextract_cli.commands._vep_run import _apply_forced_assembly
    from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary

    lib = ReferenceLibrary(None)
    lib.fasta = f"/refs/hg19/{name}"

    _apply_forced_assembly(lib, "GRCh38")

    assert lib.fasta == f"/refs/hg19/{name}"
