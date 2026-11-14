"""Regression for unnormalized ratios and unknown-sex-ploidy (issue 856)."""

from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, vcf_calls
from tests.sex_fixtures import sex_bam
from tests.test_sample_ploidy import _snp_bam
from wgsextract_cli import api
from wgsextract_cli.commands.info.metrics import infer_sex_call, parse_idxstats
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.sample_workspace import SampleWorkspace
from wgsextract_cli.core.utils import WGSExtractError

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
    bam = sex_bam(tmp_path / "S.bam", build=build, **options)
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
    bam = sex_bam(tmp_path / "S.bam")
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Female")
    result = api.info.info(
        api.InfoParams(input=Path(bam), detailed=True, outdir=tmp_path)
    )
    assert result.metrics["sex"] == "Male"
    assert result.metrics["sex_confidence"] == "medium"
    assert result.metrics["declared_sex"] == "Female"
    assert result.details is not None and result.details["sex_call"][0] == "Male"
    assert SampleWorkspace(tmp_path).overrides(bam)["sex"] == "Female"
