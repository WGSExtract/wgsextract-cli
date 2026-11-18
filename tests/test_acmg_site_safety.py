"""Site-restricted ACMG regressions from the review of issue 629."""

import gzip
import json
import threading
import time
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib
from tests.test_ref_acmg_data import (
    FIXTURES,
    _acmg,
    _acmg_run,
    _bgzf,
    _input,
    _manifest,
    _params,
    _sa,
    _write,
    needs_fastvep,
)
from tests.test_ref_acmg_data import library as library
from tests.test_ref_acmg_data import publishers as publishers
from wgsextract_cli import api
from wgsextract_cli.api.vcf import VcfAcmgParams
from wgsextract_cli.core import acmg_data
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib


@needs_fastvep
@pytest.mark.parametrize("mode", [None, "auto", "library"])
def test_629_default_and_auto_are_offline_without_site_databases(
    publishers, library, tmp_path, mode
):
    lib, _ = library
    api.ref.acmg_data(_params(library, site_data="per-run"))
    vcf = _input(tmp_path)
    before = len(publishers.requests)
    extra = {"site_data": mode} if mode else {}
    result = api.vcf.acmg(
        VcfAcmgParams(
            research_acmg=True,
            input=vcf,
            ref=lib / "genomes" / "genome.fa",
            outdir=tmp_path / "offline",
            **extra,
        ),
        check=False,
    )
    assert result.exit_code == 1
    assert "ref acmg-data" in result.error.message
    assert "--site-data per-run" in result.error.message
    assert len(publishers.requests) == before


@needs_fastvep
def test_629_missing_library_source_refuses_before_remote_reads(
    publishers, library, tmp_path
):
    lib, _ = library
    api.ref.acmg_data(_params(library, site_data="per-run"))
    for path in _sa(library).glob("revel*"):
        path.unlink()
    before = len(publishers.requests)
    result = _acmg_run(lib, _input(tmp_path), tmp_path / "out", "per-run")
    assert result.exit_code == 1
    assert "REVEL" in result.error.message
    assert len(publishers.requests) == before


def test_629_manifest_update_never_follows_predictable_partial_symlink(tmp_path):
    sa, outside = tmp_path / "sa", tmp_path / "outside"
    sa.mkdir()
    outside.write_text("unchanged")
    (sa / f"{acmg_data.MANIFEST}.partial").symlink_to(outside)
    acmg_data.record_site_data(sa, "per-run")
    assert outside.read_text() == "unchanged"
    assert json.loads((sa / acmg_data.MANIFEST).read_text())["site_data"] == "per-run"


@needs_fastvep
def test_629_normalization_skips_contigs_missing_from_reference(
    publishers, library, tmp_path
):
    lib, _ = library
    api.ref.acmg_data(_params(library, site_data="per-run"))
    vcf = _input(tmp_path)
    plain = gzip.decompress(vcf.read_bytes()).decode()
    plain = (
        plain.replace("#CHROM", "##contig=<ID=decoy,length=100>\n#CHROM")
        + "decoy\t10\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n"
    )
    extra = tmp_path / "decoy.vcf"
    extra.write_text(plain)
    extra = _bgzf(extra)
    run_command([get_tool_path("tabix"), "-p", "vcf", str(extra)])
    result = _acmg_run(lib, extra, tmp_path / "out", "per-run")
    assert result.exit_code == 0, result.error
    assert "acmg_contigs_not_classified" in {w.code for w in result.warnings}
    assert _acmg(tmp_path / "out" / "acmg_annotated.vcf.gz")[10] == {}


@needs_fastvep
def test_629_shared_cache_is_readable_without_timestamp_permission(
    publishers, library, tmp_path, monkeypatch
):
    import os

    lib, _ = library
    api.ref.acmg_data(_params(library, site_data="per-run"))
    vcf = _input(tmp_path)
    assert _acmg_run(lib, vcf, tmp_path / "first", "per-run").exit_code == 0
    original = os.utime

    def deny_cache_touch(path, *args, **kwargs):
        if Path(path).parent == _sa(library) / "sites":
            raise PermissionError("cache entry belongs to another user")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "utime", deny_cache_touch)
    before = len(publishers.gets())
    result = _acmg_run(lib, vcf, tmp_path / "again", "per-run")
    assert result.exit_code == 0, result.error
    assert len(publishers.gets()) == before
    assert _acmg(tmp_path / "again" / "acmg_annotated.vcf.gz") == _acmg(
        tmp_path / "first" / "acmg_annotated.vcf.gz"
    )


@needs_fastvep
def test_629_custom_spliceai_is_preserved_and_missing_source_never_substituted(
    publishers, library, tmp_path
):
    lib, spliceai = library
    api.ref.acmg_data(_params(library, site_data="per-run"))
    assert _manifest(library)["spliceai_file"] == str(spliceai.resolve())
    vcf = _input(tmp_path)
    result = _acmg_run(lib, vcf, tmp_path / "first", "per-run")
    assert result.exit_code == 0, result.error
    summary = json.loads((tmp_path / "first" / "acmg_summary.json").read_text())
    assert summary["site_data"]["per_run"]["spliceai"]["chr1"]["location"] == str(
        spliceai
    )
    spliceai.unlink()
    result = _acmg_run(lib, vcf, tmp_path / "missing", "per-run")
    assert result.exit_code == 1
    assert "chosen SpliceAI" in result.error.message
    assert "spliceai.vcf.gz" not in publishers.paths


@needs_fastvep
def test_629_spliceai_coverage_cannot_be_satisfied_by_another_chromosome(
    publishers, library, tmp_path
):
    lib, _ = library
    api.ref.acmg_data(_params(library, site_data="library"))
    spliceai = _write(
        tmp_path / "chr2.vcf",
        (FIXTURES / "spliceai.vcf").read_text().replace("\n1\t", "\n2\t"),
    )
    for path in _sa(library).glob("spliceai*"):
        path.unlink()
    run_command(
        [
            get_tool_path("fastvep"),
            "sa-build",
            "--source",
            "spliceai",
            "-i",
            str(spliceai),
            "-o",
            str(_sa(library) / "spliceai_chr2"),
            "--assembly",
            "GRCh38",
            "--no-progress",
        ],
        capture_output=True,
    )
    result = _acmg_run(lib, _input(tmp_path), tmp_path / "out")
    assert result.exit_code == 1
    assert "SpliceAI holds nothing of 1" in result.error.message
    assert not (tmp_path / "out" / "acmg_annotated.vcf.gz").exists()


@needs_fastvep
def test_629_concurrent_cache_builds_publish_without_deleting_the_winner(
    publishers, tmp_path, monkeypatch
):
    import os

    import numpy as np

    from wgsextract_cli.core.acmg_sites import build_databases
    from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor

    cache = tmp_path / "sites"
    barrier = threading.Barrier(2)
    original = os.replace

    def publish(source, destination, *args, **kwargs):
        if Path(source).is_dir() and Path(destination).parent == cache:
            barrier.wait(timeout=15)
        return original(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", publish)
    with ContextThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                build_databases,
                {"gnomad": ["1"]},
                {"1": np.array([[1149, 1151]])},
                None,
                cache,
                1,
            )
            for _ in range(2)
        ]
        results = [f.result() for f in futures]
    assert results[0][0].folder == results[1][0].folder
    assert results[0][0].paths[0].read_bytes() == results[1][0].paths[0].read_bytes()
    assert len(list(cache.iterdir())) == 1


@needs_fastvep
def test_629_spliceai_failure_promptly_cancels_the_other_source(
    publishers, tmp_path, monkeypatch
):
    import numpy as np

    from wgsextract_cli.core import acmg_sites, tabix_sites
    from wgsextract_cli.core.utils import WGSExtractError

    reading = threading.Event()
    original_read, original_open = tabix_sites.ByteSource.read, tabix_sites.urlopen

    def wait_for_cancellation(source, start, end):
        if "/gnomad/" in source.location:
            reading.set()
            deadline = time.monotonic() + 5
            while not source.cancel.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not source.cancel.is_set():
                raise WGSExtractError("other source was not promptly cancelled")
        return original_read(source, start, end)

    def missing_spliceai(request, *args, **kwargs):
        if request.full_url.endswith("/spliceai.vcf.gz"):
            assert reading.wait(5)
        return original_open(request, *args, **kwargs)

    publishers.statuses["spliceai.vcf.gz"] = 404
    monkeypatch.setattr(tabix_sites.ByteSource, "read", wait_for_cancellation)
    monkeypatch.setattr(tabix_sites, "urlopen", missing_spliceai)
    with pytest.raises(WGSExtractError, match="SpliceAI"):
        acmg_sites.build_databases(
            {"gnomad": ["1"], "spliceai": ["1"]},
            {"1": np.array([[1149, 1151]])},
            None,
            tmp_path / "sites",
            1,
        )
