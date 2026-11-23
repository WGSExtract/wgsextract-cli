"""Use real fastVEP to verify that region tracks change ACMG evidence."""

import json

import pytest

from tests.htslib_helpers import needs_htslib
from tests.test_ref_acmg_data import (
    FIXTURES,
    _acmg,
    _acmg_run,
    _bgzf,
    _params,
    _write,
    needs_fastvep,
)
from tests.test_ref_acmg_data import library as library
from tests.test_ref_acmg_data import publishers as publishers
from wgsextract_cli import api
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib


def test_1458_acmg_data_requires_opt_in_even_for_remote_size_probes(
    publishers, library
):
    preview = api.ref.acmg_data(_params(library, dry_run=True, download=False))
    assert "UCSC RepeatMasker" in preview.text
    result = api.ref.acmg_data(_params(library, download=False), check=False)
    assert result.exit_code != 0 and result.error
    assert (
        "RepeatMasker" in result.error.message and "--download" in result.error.message
    )
    assert publishers.requests == []


@needs_fastvep
@pytest.mark.parametrize("site_data", ["library", "per-run"])
def test_1458_bp3_and_region_flags_reach_fastvep(
    publishers, library, tmp_path, site_data
):
    lib, _ = library
    api.ref.acmg_data(_params(library, site_data=site_data))
    template = (FIXTURES / "in_vep.vcf").read_text()
    extra = (
        "1\t1023\t.\tTGTG\tT\t50\tPASS\t.\tGT\t0/1\n"
        "1\t1050\t.\tATAA\tA\t50\tPASS\t.\tGT\t0/1\n"
        "1\t1119\t.\tAC\tA\t50\tPASS\t.\tGT\t0/1\n"
        "1\t1120\t.\tC\tG\t50\tPASS\t.\tGT\t0/1\n"
        "1\t1121\t.\tC\tG\t50\tPASS\t.\tGT\t0/1\n"
        "1\t1130\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n"
    )
    vcf = _bgzf(
        _write(
            tmp_path / "regions.vcf", template.replace("1\t1150\t", extra + "1\t1150\t")
        )
    )
    run_command([get_tool_path("tabix"), "-p", "vcf", str(vcf)])
    result = _acmg_run(lib, vcf, tmp_path / "out", site_data=site_data)
    assert result.exit_code == 0, result.error

    calls = _acmg(tmp_path / "out" / "acmg_annotated.vcf.gz")
    criteria = {
        pos: set(info["ACMG_CRITERIA"].split("&")) for pos, info in calls.items()
    }
    assert "BP3" in criteria[1023]
    assert "PM4" in criteria[1050]  # the outside record is also an in-frame indel
    assert "BP3" not in criteria[1050]  # interspersed Alu is not a simple repeat
    assert "BA1" in criteria[1119]  # REF crosses the LCR boundary, but POS is outside
    assert "BA1" not in criteria[1120]  # single-base LCR end is inclusive
    assert "BA1" in criteria[1121]
    assert "BA1" not in criteria[1130]  # BED segdup ends at 1-based position 1130
    assert "BA1" in criteria[1150]
    assert result.details["gnomad_region_flags_not_evaluated"] == {}
    summary = json.loads((tmp_path / "out" / "acmg_summary.json").read_text())
    assert "BP3" not in summary["criteria_not_evaluated"]
    assert summary["gnomad_region_flags_not_evaluated"] == {}
    assert "PM2_Supporting" in summary["gnomad_region_flags_scope"]
    assert "acmg_gnomad_region_flags_not_evaluated" not in {
        w.code for w in result.warnings
    }


@needs_fastvep
def test_1458_restoring_the_same_track_does_not_stream_gnomad_again(
    publishers, library
):
    api.ref.acmg_data(_params(library))
    data = library[0] / "downloads" / "acmg" / "sa"
    index = data / "gnomad_lcr.bed.gz.tbi"
    index.unlink()
    publishers.requests.clear()
    api.ref.acmg_data(_params(library))
    assert index.is_file()
    assert publishers.gets() == []
