"""`score pgs` runs bcftools as its dependency check found it (#1233)."""

import shutil
import sys
from pathlib import Path

import pytest

from tests.fake_tools import FakePixi
from wgsextract_cli import api

FIXTURES = Path(__file__).parent / "fixtures" / "pgs"
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bcftools"),
    reason="Pixi executable script fixture; needs real bcftools",
)


def _params(vcf: Path, outdir: Path, sample: str | None = None) -> api.ScorePgsParams:
    return api.ScorePgsParams(
        input=vcf,
        build="GRCh38",
        scoring_file=FIXTURES / "additive.txt",
        outdir=outdir,
        sample=sample,
    )


@pytest.fixture
def managed_bcftools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #1233: bcftools only in the CLI's Pixi environment, not on PATH.
    Dependency checks found it there, but the sample and header probes ran a
    bare `bcftools` and failed with "No such file or directory"."""
    real = shutil.which("bcftools")
    assert real
    pixi = FakePixi(
        tmp_path / "pixi", Path(__file__).resolve().parents[1] / "pixi.toml"
    )
    prefix = pixi.env("default", locked=("bcftools",))
    (prefix / "bin" / "bcftools").symlink_to(real)
    monkeypatch.setenv("PIXI_EXE", str(pixi.exe))
    monkeypatch.setenv("PATH", str(pixi.host_bin))
    monkeypatch.delenv("PIXI_ENVIRONMENT_NAME", raising=False)


def _two_samples(path: Path, chr1_length: int = 100) -> Path:
    """sample.vcf with a second sample, OTHER, homozygous for the effect
    allele at chr1:10 (a score of 1.5 rather than SAMPLE's 1.0)."""
    lines = []
    for line in (FIXTURES / "sample.vcf").read_text().splitlines():
        if line.startswith("##contig"):
            line = f"##contig=<ID=chr1,length={chr1_length}>"
        elif line.startswith("#CHROM"):
            line += "\tOTHER"
        elif not line.startswith("#"):
            fields = line.split("\t")
            line += "\t" + ("1/1:50:30" if fields[1] == "10" else fields[-1])
        lines.append(line)
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.mark.usefixtures("managed_bcftools")
def test_1233_managed_bcftools_selects_the_sample_and_scores(tmp_path: Path) -> None:
    vcf = _two_samples(tmp_path / "two.vcf")
    unselected = api.score.pgs(_params(vcf, tmp_path / "a"), check=False)
    assert unselected.exit_code == 2
    assert unselected.error is not None and "--sample" in unselected.error.message

    result = api.score.pgs(_params(vcf, tmp_path / "b", sample="OTHER"))
    assert result.metrics["score_sum"] == pytest.approx(1.5)


@pytest.mark.usefixtures("managed_bcftools")
def test_1233_managed_bcftools_header_still_checks_the_build(tmp_path: Path) -> None:
    vcf = _two_samples(tmp_path / "grch37.vcf", chr1_length=249250621)
    result = api.score.pgs(_params(vcf, tmp_path, sample="SAMPLE"), check=False)
    assert result.exit_code == 2
    assert result.error is not None and "--build" in result.error.message


@pytest.mark.usefixtures("managed_bcftools")
def test_1233_unreadable_vcf_reports_what_bcftools_said(tmp_path: Path) -> None:
    vcf = tmp_path / "broken.vcf"
    vcf.write_text("not a VCF\n")
    result = api.score.pgs(_params(vcf, tmp_path), check=False)
    assert result.error is not None
    assert "Cannot read VCF" in result.error.message
    assert "broken.vcf" in result.error.message
