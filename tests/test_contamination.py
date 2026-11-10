"""`qc contamination` (#616): VerifyBamID2's FREEMIX, recorded as the sample's
`contamination` fact, and what that fact changes downstream: genotype QC
warnings and confidence labels."""

import json
import logging
import shlex
from pathlib import Path

import pytest

from tests.contamination_helpers import READ as READ_LEN
from tests.contamination_helpers import build_panel, population, reads_bam
from tests.htslib_helpers import needs_htslib, sam_to_bam
from wgsextract_cli import api
from wgsextract_cli.core import contamination, sample_workspace
from wgsextract_cli.core.genotype_qc import AUTOSOMES, warn_low_depth
from wgsextract_cli.core.pixi_tools import pixi_env_tool
from wgsextract_cli.core.results import CommandResult, collecting

from .test_variant_confidence import CALLS, _labels, _run, _vcf


def _genome_folder(tmp_path: Path, freemix: float) -> tuple[Path, Path]:
    """A genome folder whose BAM `qc contamination` measured at ``freemix``."""
    folder = tmp_path / "genome"
    folder.mkdir()
    (folder / "genome-config.toml").write_text("")
    bam = folder / "sample.bam"
    bam.write_bytes(b"BAM\x01")
    assert sample_workspace.SampleWorkspace(folder).record(
        contamination.FACT, bam, freemix, method="VerifyBamID2", confidence="high"
    )
    return folder, bam


@needs_htslib
@pytest.mark.parametrize(("freemix", "label"), [(0.08, "moderate"), (0.004, "high")])
def test_contaminated_sample_caps_its_findings_at_moderate(tmp_path, freemix, label):
    folder, _bam = _genome_folder(tmp_path, freemix)
    sample = _vcf(folder / "s.vcf.gz", CALLS[:1])
    gnomad = _vcf(
        tmp_path / "gnomad.vcf.gz",
        ["100 . C T . . AF=0.0001 GT 0/1"],
        header='##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n',
    )
    document = _run(tmp_path, "vcf", "gnomad", "--vcf-input", sample, "--gnomad-file",
                    gnomad, "--max-af", "0.01", "--outdir", str(folder))  # fmt: skip
    reasons = "sample_contaminated" if label == "moderate" else "."
    assert _labels(folder / "gnomad_af_lt_0.01.vcf.gz") == [f"100:{label}:{reasons}"]
    codes = {w["code"] for w in document["warnings"]}
    assert (contamination.WARNING in codes) == (label == "moderate")


@needs_htslib
def test_findings_written_outside_the_genome_folder_are_capped_too(tmp_path):
    """PR #631 review: `vcf gnomad --outdir elsewhere` labels intermediates
    outside the genome folder; the input's estimate still applies."""
    folder, _bam = _genome_folder(tmp_path, 0.08)
    sample = _vcf(folder / "s.vcf.gz", CALLS[:1])
    gnomad = _vcf(
        tmp_path / "gnomad.vcf.gz",
        ["100 . C T . . AF=0.0001 GT 0/1"],
        header='##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n',
    )
    out = tmp_path / "elsewhere"
    document = _run(tmp_path, "vcf", "gnomad", "--vcf-input", sample, "--gnomad-file",
                    gnomad, "--max-af", "0.01", "--outdir", str(out))  # fmt: skip
    assert _labels(out / "gnomad_af_lt_0.01.vcf.gz") == [
        "100:moderate:sample_contaminated"
    ]
    assert contamination.WARNING in {w["code"] for w in document["warnings"]}


@needs_htslib
def test_genotype_qc_warns_about_a_contaminated_alignment(tmp_path):
    folder, _ = _genome_folder(tmp_path, 0.06)
    seq = "ACGT" * 25
    bam = sam_to_bam(
        ["@SQ\tSN:chr1\tLN:1000",
         f"r1\t0\tchr1\t1\t60\t100M\t*\t0\t0\t{seq}\t{'I' * 100}"],
        folder / "other.bam",
    )  # fmt: skip
    result = CommandResult("test")
    with collecting(result):
        warn_low_depth(bam, {AUTOSOMES}, min_depth=0)
    [warning] = [w for w in result.warnings if w.code == contamination.WARNING]
    assert warning.details["freemix"] == 0.06


@needs_htslib
def test_t2t_input_needs_its_own_panel(tmp_path, caplog):
    bam = sam_to_bam(["@SQ\tSN:chr1\tLN:248387328"], tmp_path / "t2t.bam")
    result = api.qc.contamination(
        api.QcContaminationParams(input=Path(bam)), check=False
    )
    assert result.exit_code == 1
    assert "T2T" in result.error.message and "--svd-prefix" in result.error.message


# --- the real VerifyBamID2, on a synthetic population ---------------------------


def _verifybamid2() -> list[str] | None:
    command = pixi_env_tool("verifybamid2")
    return shlex.split(command) if command else None


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    command = _verifybamid2()
    if command is None:
        pytest.skip("VerifyBamID2 isn't installed (pixi install -e verifybamid2)")
    pop = population(tmp_path_factory.mktemp("population"))
    return pop, build_panel(pop, command)


@needs_htslib
@pytest.mark.parametrize(
    ("fraction", "low", "high"), [(0.0, 0.0, 0.02), (0.10, 0.07, 0.13)]
)
def test_freemix_measures_a_mixture_and_is_recorded(
    tmp_path, panel, fraction, low, high
):
    pop, prefix = panel
    folder = tmp_path / "genome"
    folder.mkdir()
    (folder / "genome-config.toml").write_text("")
    bam = reads_bam(pop, folder / "s.bam", 0, 1 if fraction else None, fraction)
    result = api.qc.contamination(
        api.QcContaminationParams(
            input=bam, ref=pop["fasta"], svd_prefix=str(prefix), threads=1
        )
    )
    freemix = result.metrics["freemix"]
    assert low <= freemix <= high
    report = folder / "s.contamination.selfSM"
    assert report.is_file() and (folder / "s.contamination.Ancestry").is_file()
    assert (
        json.loads(Path(result.manifest).read_text())["metrics"]["freemix"] == freemix
    )
    fact = sample_workspace.SampleWorkspace(folder).fact(contamination.FACT, bam)
    assert fact is not None and fact.value == freemix
    codes = {w.code for w in result.warnings}
    assert (contamination.WARNING in codes) == (fraction > 0)


@needs_htslib
def test_a_missing_svd_panel_is_a_usage_error(tmp_path, panel, caplog):
    pop, _prefix = panel
    bam = reads_bam(pop, tmp_path / "s.bam", 0)
    with caplog.at_level(logging.ERROR):
        result = api.qc.contamination(
            api.QcContaminationParams(
                input=bam, ref=pop["fasta"], svd_prefix=str(tmp_path / "none")
            ),
            check=False,
        )
    assert result.exit_code == 2
    assert ".UD" in result.error.message


def _renamed_panel(prefix: str, folder: Path, rename) -> str:
    """A copy of the panel with its contigs renamed (chr20 to 20, say)."""
    import shutil

    folder.mkdir()
    local = str(folder / "panel")
    for suffix, sep in ((".bed", "\t"), (".mu", ":")):
        lines = Path(prefix + suffix).read_text().splitlines(keepends=True)
        Path(local + suffix).write_text(
            "".join(
                rename(ln.partition(sep)[0]) + sep + ln.partition(sep)[2]
                for ln in lines
            )
        )
    for suffix in (".UD", ".V"):
        if Path(prefix + suffix).is_file():
            shutil.copyfile(prefix + suffix, local + suffix)
    return local


@needs_htslib
def test_a_chromosome_only_bam_matches_a_panel_named_without_chr(tmp_path, panel):
    """PR #631 review: a chr20-only BAM (no chr1 to tell its naming) against
    a panel naming the contig 20."""
    pop, prefix = panel
    numeric = _renamed_panel(
        prefix, tmp_path / "numeric", lambda c: c.removeprefix("chr")
    )
    bam = reads_bam(pop, tmp_path / "s.bam", 0, 1, 0.10)
    result = api.qc.contamination(
        api.QcContaminationParams(
            input=bam, ref=pop["fasta"], svd_prefix=numeric, threads=1
        )
    )
    assert 0.07 <= result.metrics["freemix"] <= 0.13


@needs_htslib
def test_no_reads_at_panel_sites_records_no_estimate(tmp_path, panel):
    """PR #631 review: an extract with no reads at any panel site (mtDNA only,
    full sequence dictionary) must not record a meaningless FREEMIX."""
    pop, prefix = panel
    folder = tmp_path / "genome"
    folder.mkdir()
    (folder / "genome-config.toml").write_text("")
    seq = pop["seq"][:READ_LEN]
    bam = Path(sam_to_bam(
        ["@SQ\tSN:chr20\tLN:1200000", "@SQ\tSN:chrM\tLN:16569",
         f"r1\t0\tchrM\t1\t60\t{READ_LEN}M\t*\t0\t0\t{seq}\t{'I' * READ_LEN}"],
        folder / "mt.bam",
    ))  # fmt: skip
    result = api.qc.contamination(
        api.QcContaminationParams(
            input=bam, ref=pop["fasta"], svd_prefix=prefix, threads=1
        ),
        check=False,
    )
    assert result.exit_code == 1
    assert "no reads" in result.error.message
    assert "freemix" not in result.metrics
    assert (
        sample_workspace.SampleWorkspace(folder).fact(contamination.FACT, bam) is None
    )
