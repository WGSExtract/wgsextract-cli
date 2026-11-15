"""#652: a BAM whose read groups mix platforms (ONT and Illumina merged) must
not silently take one platform's defaults: commands whose model, preset or
caller depends on the platform refuse it unless told which to use, and the
others warn."""

import logging
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, run_wgsextract, snp_sample
from wgsextract_cli import api
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import UsageError, run_command

pytestmark = needs_tools("samtools", "bcftools", "tabix")

ILLUMINA = "@RG\tID:rg\tSM:S\tPL:ILLUMINA"


@pytest.fixture
def reads(tmp_path):
    """An Illumina sample, and the same reads merged with an ONT read group."""
    sample = snp_sample(tmp_path / "illumina", ILLUMINA)
    samtools = get_tool_path("samtools")
    ont = tmp_path / "ont.bam"
    run_command(
        [samtools, "addreplacerg", "-r", "@RG\tID:ont\tSM:S\tPL:ONT",
         "-o", str(ont), sample.bam]
    )  # fmt: skip
    mixed = tmp_path / "mixed.bam"
    run_command([samtools, "merge", "-o", str(mixed), sample.bam, str(ont)])
    run_command([samtools, "index", str(mixed)])
    sample.mixed = str(mixed)
    return sample


def warning_codes(result):
    return [warning.code for warning in result.warnings]


def test_info_reports_the_mixed_platforms(reads, tmp_path):
    result = api.info.info(
        api.InfoParams(input=Path(reads.mixed), ref=Path(reads.ref), outdir=tmp_path)
    )
    assert result.metrics["platforms"] == "illumina,ont"
    assert result.details["platforms"] == ["illumina", "ont"]
    assert "mixed_platforms" in warning_codes(result)
    assert "Mixed: illumina, ont" in result.text


def test_info_on_one_platform_is_unchanged(reads, tmp_path):
    result = api.info.info(
        api.InfoParams(input=Path(reads.bam), ref=Path(reads.ref), outdir=tmp_path)
    )
    assert "platforms" not in result.metrics
    assert "platforms" not in result.details
    assert "mixed_platforms" not in warning_codes(result)


def test_bcftools_calling_warns_and_still_calls(reads, tmp_path):
    result = api.vcf.snp(
        api.VcfSnpParams(
            input=Path(reads.mixed), ref=Path(reads.ref), outdir=tmp_path, ploidy="2"
        )
    )
    [warning] = [w for w in result.warnings if w.code == "mixed_platforms"]
    assert warning.details["platforms"] == "illumina,ont"
    assert any(o.role == "snps" for o in result.outputs)


def test_bcftools_calling_on_one_platform_does_not_warn(reads, tmp_path):
    result = api.vcf.snp(
        api.VcfSnpParams(
            input=Path(reads.bam), ref=Path(reads.ref), outdir=tmp_path, ploidy="2"
        )
    )
    assert "mixed_platforms" not in warning_codes(result)


def test_sv_calling_refuses_to_pick_a_caller(reads, tmp_path):
    with pytest.raises(UsageError, match="--caller") as error:
        run_wgsextract(
            "vcf", "sv", "--input", reads.mixed, "--ref", reads.ref,
            "--outdir", str(tmp_path),
        )  # fmt: skip
    assert "illumina, ont" in str(error.value)


def test_smn_typing_refuses_mixed_platforms_with_one_message(reads, tmp_path):
    # SMNCopyNumberCaller is short-read only: no option can pick a platform.
    with pytest.raises(UsageError, match="short-read") as error:
        run_wgsextract(
            "typing", "smn", "--input", reads.mixed, "--ref", reads.ref,
            "--outdir", str(tmp_path),
        )  # fmt: skip
    assert "illumina, ont" in str(error.value)
    assert "samtools split" in str(error.value)
    assert not list(tmp_path.glob("*.smn.*"))


@pytest.mark.parametrize("command", ["align", "realign"])
def test_alignment_refuses_to_pick_a_preset(reads, tmp_path, command):
    reads_option = "--r1" if command == "align" else "--input"
    with pytest.raises(UsageError, match="--platform") as error:
        run_wgsextract(
            command, reads_option, reads.mixed, "--ref", reads.ref,
            "--outdir", str(tmp_path),
        )  # fmt: skip
    assert "illumina, ont" in str(error.value)


def test_read_groups_differing_only_in_pl_case_are_one_platform(tmp_path):
    sample = snp_sample(
        tmp_path, "@RG\tID:rg\tSM:S\tPL:PacBio\n@RG\tID:b\tSM:S\tPL:PACBIO"
    )
    result = api.info.info(
        api.InfoParams(input=Path(sample.bam), ref=Path(sample.ref), outdir=tmp_path)
    )
    assert "platforms" not in result.metrics
    assert "mixed_platforms" not in warning_codes(result)


def test_sv_calling_on_a_missing_input_gives_the_missing_path_error(tmp_path, caplog):
    """The mixed-platform check must not read a missing file's header first."""
    caplog.set_level(logging.DEBUG)
    missing = tmp_path / "missing.bam"
    result = api.vcf.sv(api.VcfSvParams(input=missing, outdir=tmp_path), check=False)
    assert result.exit_code and "missing.bam" in result.error.message
    assert "Failed to fetch BAM header" not in caplog.text
