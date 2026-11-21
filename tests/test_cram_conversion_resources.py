"""#1123: Windows CRAM RAM reflects reference residency, not just codecs."""

import gzip
import platform
from pathlib import Path

import pytest

from tests.prepared_data import sparse_truncate
from tests.test_resource_estimates import HS38DH
from wgsextract_cli.core.resource_estimates import PreflightEstimate, estimate_resources

GIB = 1024**3


def _estimate(
    reference: Path, directory: Path, region: str | None = None
) -> PreflightEstimate:
    source = directory / "input.cram"
    source.write_bytes(b"x" * 1200)
    return estimate_resources(
        "to-cram",
        threads="7",
        input_paths=[str(source)],
        output_dir=str(directory),
        reference=str(reference),
        region=region,
        available_ram=2 * GIB,
    )


@pytest.mark.parametrize("region", ["chrM", "chr1:100-"])
def test_1195_regional_cram_unknown_m5_budgets_whole_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, region: str
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    reference = tmp_path / "ref.fa"
    reference.write_text(">x\n")
    Path(f"{reference}.fai").write_text(
        "".join(f"{name}\t{length}\t0\t60\t61\n" for name, length in HS38DH.items())
    )
    report = _estimate(reference, tmp_path)
    assert report.resources[0].status == "LOW"
    needed = report.resources[0].estimated_bytes
    assert needed is not None and needed > 2 * GIB
    regional = _estimate(reference, tmp_path, region)
    assert regional.resources[0].estimated_bytes == needed
    assert regional.resources[0].status == "LOW"
    assert any(resource.resource == "disk" for resource in regional.resources)


def test_1123_unindexed_plain_reference_is_not_a_zero_memory_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    reference = tmp_path / "unindexed.fa"
    sparse_truncate(reference, 3 * GIB)
    report = _estimate(reference, tmp_path)
    assert report.resources[0].status == "LOW"
    assert any(".fai unavailable" in note for note in report.notes)


def test_1123_unindexed_compressed_reference_reports_unknown_ram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    reference = tmp_path / "unindexed.fa.gz"
    reference.write_bytes(gzip.compress(b">chr1\nACGT\n"))
    report = _estimate(reference, tmp_path)
    assert report.resources[0].estimated_bytes is None
    assert report.resources[0].status == "UNKNOWN"
    assert any("reference-cache RAM is unknown" in note for note in report.notes)
