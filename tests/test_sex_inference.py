"""Regression for unnormalized ratios and unknown-sex-ploidy (issue 856)."""

import json
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, vcf_calls
from tests.sex_fixtures import distributed_alignment
from tests.test_sample_ploidy import _snp_bam
from wgsextract_cli import api
from wgsextract_cli.commands.info.metrics import (
    infer_sex_call,
    parse_idxstats,
    sample_sex,
)
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.sample_workspace import SampleWorkspace
from wgsextract_cli.core.utils import WGSExtractError, run_command

pytestmark = needs_htslib


@pytest.mark.parametrize("build", ["GRCh37", "GRCh38"])
@pytest.mark.parametrize(
    ("options", "sex"),
    [
        ({"x_depth": 4, "y_depth": 0}, "Female"),
        ({}, "Male"),
        ({"y_depth": 0}, "Unknown"),  # X0-like: one X does not imply XY
        ({"auto_depth": 0}, "Unknown"),
        ({"auto_depth": 1, "x_depth": 1, "y_depth": 0}, "Unknown"),
        ({"narrow_x": True}, "Unknown"),  # unequal capture breadth
        ({"par_only": True}, "Unknown"),
        ({"extra_flags": 1024}, "Unknown"),  # duplicated reads are not support
    ],
)
def test_issue_856_nonpar_normalization_on_fake_genomes(
    tmp_path: Path, build: str, options: dict[str, int | bool], sex: str
) -> None:
    bam = distributed_alignment(tmp_path / "S.bam", build=build, **options)
    rows, *_ = parse_idxstats(bam)
    call = infer_sex_call(rows, alignment=bam)
    assert call.sex == sex
    assert call.confidence == ("low" if sex == "Unknown" else "medium")
    assert infer_sex_call(rows).sex == "Unknown"


def test_issue_856_unknown_sex_ploidy_refuses_until_explicit_override(
    tmp_path: Path,
) -> None:
    ref, bam = _snp_bam(tmp_path / "sample", "chrX", "chrY", "chr7")
    out = tmp_path / "out"
    with pytest.raises(WGSExtractError, match="Unknown sex"):
        run_wgsextract(
            "vcf",
            "snp",
            "--input",
            bam,
            "--ref",
            ref,
            "--outdir",
            str(out),
            "--ploidy",
            "GRCh38",
            "--threads",
            "1",
        )
    assert not (out / "snps.vcf.gz").exists()
    observed = SampleWorkspace(tmp_path / "sample").detected("sex", bam)
    assert observed is not None
    assert observed.value == "Unknown" and observed.confidence == "low"
    numeric_out = tmp_path / "numeric"
    run_wgsextract(
        "vcf",
        "snp",
        "--input",
        bam,
        "--ref",
        ref,
        "--outdir",
        str(numeric_out),
        "--ploidy",
        "2",
        "--threads",
        "1",
    )
    assert all(row[-1] == "1/1" for row in vcf_calls(numeric_out / "snps.vcf.gz"))
    ploidy_file = tmp_path / "diploid.ploidy"
    ploidy_file.write_text("* * * M 2\n* * * F 2\n")
    custom_out = tmp_path / "custom"
    run_wgsextract(
        "vcf",
        "snp",
        "--input",
        bam,
        "--ref",
        ref,
        "--outdir",
        str(custom_out),
        "--ploidy-file",
        str(ploidy_file),
        "--threads",
        "1",
    )
    assert all(row[-1] == "1/1" for row in vcf_calls(custom_out / "snps.vcf.gz"))
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Male")
    result = CommandResult(command="vcf snp")
    with collecting(result):
        run_wgsextract(
            "vcf",
            "snp",
            "--input",
            bam,
            "--ref",
            ref,
            "--outdir",
            str(out),
            "--ploidy",
            "GRCh38",
            "--threads",
            "1",
        )
    assert result.metrics["observed_sex"] == "Unknown"
    assert result.metrics["sex_confidence"] == "low"
    assert result.metrics["declared_sex"] == result.metrics["effective_sex"] == "Male"
    calls = vcf_calls(out / "snps.vcf.gz")
    assert next(row for row in calls if row[0] == "chrY")[-1] == "1"
    observed = SampleWorkspace(tmp_path / "sample").detected("sex", bam)
    assert observed is not None and observed.value == "Unknown"


def test_issue_856_info_preserves_observed_and_declared_sex(tmp_path: Path) -> None:
    bam = distributed_alignment(tmp_path / "S.bam")
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Female")
    result = api.info.info(
        api.InfoParams(input=Path(bam), detailed=True, outdir=tmp_path)
    )
    assert result.metrics["sex"] == "Male"
    assert result.metrics["sex_confidence"] == "medium"
    assert result.metrics["declared_sex"] == "Female"
    assert result.details is not None and result.details["sex_call"][0] == "Male"
    assert SampleWorkspace(tmp_path).overrides(bam)["sex"] == "Female"


def test_issue_856_unknown_measurement_recovers_after_index_restored(
    tmp_path: Path,
) -> None:
    bam = distributed_alignment(tmp_path / "S.bam")
    run_wgsextract("sample", "set", "--input", bam, "--platform", "illumina")
    Path(bam + ".bai").unlink()
    first = api.info.info(
        api.InfoParams(input=Path(bam), detailed=True, outdir=tmp_path)
    )
    assert first.metrics["sex"] == "Unknown"
    assert SampleWorkspace(tmp_path).detected("sex", bam) is None
    run_command(["samtools", "index", bam])
    assert sample_sex(bam) == "Male"
    second = api.info.info(
        api.InfoParams(input=Path(bam), detailed=True, outdir=tmp_path)
    )
    assert second.metrics["sex"] == "Male"
    observed = SampleWorkspace(tmp_path).detected("sex", bam)
    assert observed is not None and observed.confidence == "medium"


def test_issue_856_fast_info_discards_retired_sex_cache(tmp_path: Path) -> None:
    bam = distributed_alignment(tmp_path / "S.bam")
    api.info.info(api.InfoParams(input=Path(bam), detailed=True, outdir=tmp_path))
    cache = Path(bam + ".wgse_info.json")
    old = json.loads(cache.read_text())
    old.update(
        sex_inference_version=2,
        gender="Female",
        sex_call=["Female", "raw X/Y reads", "high"],
    )
    cache.write_text(json.dumps(old))
    result = api.info.info(api.InfoParams(input=Path(bam), outdir=tmp_path))
    assert "sex" not in result.metrics
    assert result.details is not None and "sex_call" not in result.details


def test_issue_856_unavailable_sex_keeps_other_cached_info(tmp_path: Path) -> None:
    bam = distributed_alignment(tmp_path / "S.bam")
    Path(bam + ".bai").unlink()
    api.info.info(api.InfoParams(input=Path(bam), detailed=True, outdir=tmp_path))
    cache = Path(bam + ".wgse_info.json")
    data = json.loads(cache.read_text())
    data["avg_read_len"] = 123
    cache.write_text(json.dumps(data))
    result = api.info.info(
        api.InfoParams(input=Path(bam), detailed=True, outdir=tmp_path)
    )
    assert result.metrics["avg_read_len"] == 123
    assert result.metrics["sex"] == "Unknown"


def test_issue_856_multisample_detection_does_not_publish_last_sample_sex(
    tmp_path: Path,
) -> None:
    a = distributed_alignment(tmp_path / "a.bam")
    b = distributed_alignment(tmp_path / "b.bam", x_depth=4, y_depth=0)
    result = CommandResult(command="qc relate")
    with collecting(result):
        assert sample_sex(a) == "Male"
        assert sample_sex(b) == "Female"
    assert (
        "observed_sex" not in result.metrics and "effective_sex" not in result.metrics
    )
