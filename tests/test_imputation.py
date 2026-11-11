"""`vcf impute` and `ref imputation` on real HG002 data (#621).

tests/fixtures/imputation_hg002 (provenance.json) holds, for GRCh38
chr20:10,000,000-10,300,000: HG002 Illumina reads downsampled to about 1x, the
1000 Genomes 30x panel's common biallelic SNPs there (all 3,202 samples, GT
only), GLIMPSE's genetic map, GIAB v4.2.1's calls and confident regions, and a
23andMe v5-like chip VCF of HG002's truth genotypes at the chip's sites, with
contigs named 20 as chip exports name them. The panel and map stand in for the
pinned downloads; GLIMPSE2, Beagle and bcftools run for real.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, needs_tools
from wgsextract_cli import api
from wgsextract_cli.core import imputation_panel
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.pixi_tools import pixi_env_prefix
from wgsextract_cli.core.utils import UsageError

FIXTURE = Path(__file__).parent / "fixtures" / "imputation_hg002"


def _query(vcf: Path, fmt: str, *args: str) -> list[list[str]]:
    out = subprocess.run(
        [get_tool_path("bcftools"), "query", *args, "-f", fmt, str(vcf)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.split("\t") for line in out.splitlines()]


def _header(vcf: Path) -> str:
    return subprocess.run(
        [get_tool_path("bcftools"), "view", "-h", str(vcf)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _dosage(gt: str) -> int:
    return sum(allele == "1" for allele in gt.replace("|", "/").split("/"))


def _truth() -> dict[int, int]:
    """Alt dosage at every confident panel site (0 where GIAB has no call)."""
    bed = [
        tuple(map(int, line.split()[1:3]))
        for line in (FIXTURE / "truth.bed").read_text().splitlines()
    ]
    calls = {
        int(pos): (ref, alt, _dosage(gt))
        for pos, ref, alt, gt in _query(
            FIXTURE / "truth.vcf.gz", "%POS\t%REF\t%ALT\t[%GT]\n"
        )
    }
    truth = {}
    for pos, ref, alt in _query(FIXTURE / "panel.vcf.gz", "%POS\t%REF\t%ALT\n"):
        p = int(pos)
        if not any(start < p <= end for start, end in bed):
            continue
        if p in calls and calls[p][:2] != (ref, alt):
            continue
        truth[p] = calls[p][2] if p in calls else 0
    return truth


def _concordance(vcf: Path) -> tuple[float, int]:
    """Genotype concordance of the confident (PASS) imputed calls with GIAB at
    its confident panel sites, and how many such sites there are."""
    truth = _truth()
    calls = {
        int(pos): _dosage(gt)
        for pos, gt in _query(vcf, "%POS\t[%GT]\n", "-f", "PASS")
        if int(pos) in truth
    }
    agree = sum(calls[p] == truth[p] for p in calls)
    return agree / len(calls), len(calls)


@pytest.fixture
def panel_downloads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Serve the fixture's panel and map for the pinned downloads; returns the
    URLs fetched."""
    fetched: list[str] = []

    def fetch(url: str, dest: str, *args: object, **kwargs: object) -> bool:
        fetched.append(url)
        source = "chr20.b38.gmap.gz" if url.endswith(".gmap.gz") else "panel.vcf.gz"
        shutil.copy(FIXTURE / source, dest)
        return True

    monkeypatch.setattr(imputation_panel, "download_file", fetch)
    # The fixture's 300 kb is smaller than one default GLIMPSE2 window.
    monkeypatch.setattr(
        imputation_panel,
        "CHUNK_OPTIONS",
        ("--window-mb", "0.1", "--buffer-mb", "0.02", "--window-cm", "0.05",
         "--buffer-cm", "0.01", "--window-count", "200", "--buffer-count", "20"),
    )  # fmt: skip
    return fetched


@pytest.fixture
def reflib(tmp_path: Path, panel_downloads: list[str]) -> Path:
    library = tmp_path / "reference"
    library.mkdir()
    api.ref.imputation(
        api.RefImputationParams(chromosomes="chr20", ref=library, threads=2)
    )
    return library


needs_glimpse = needs_tools("GLIMPSE2_phase", "GLIMPSE2_ligate", "GLIMPSE2_chunk")


@needs_htslib
@needs_glimpse
def test_ref_imputation_installs_a_chromosome_once(reflib, panel_downloads):
    assert imputation_panel.installed_chromosomes(str(reflib)) == ["chr20"]
    fetched = len(panel_downloads)

    api.ref.imputation(api.RefImputationParams(chromosomes="20", ref=reflib, threads=2))

    assert len(panel_downloads) == fetched
    api.ref.imputation(
        api.RefImputationParams(chromosomes="chr20", ref=reflib, delete=True)
    )
    assert imputation_panel.installed_chromosomes(str(reflib)) == []


@needs_htslib
@needs_glimpse
def test_glimpse2_imputes_1x_reads_and_labels_them(reflib, tmp_path):
    result = api.vcf.impute(
        api.VcfImputeParams(
            input=FIXTURE / "reads.bam",
            ref=reflib,
            outdir=tmp_path / "out",
            threads=2,
        )
    )

    (output,) = [Path(o.path) for o in result.outputs if o.role == "imputed_variants"]
    header = _header(output)
    assert "##wgsextract_imputation=<Tool=GLIMPSE2" in header
    assert "##FILTER=<ID=ImpLowConf" in header
    rows = _query(output, "%INFO/IMP\t%FILTER\n")
    assert rows and all(imp == "1" for imp, _f in rows)
    metrics = result.metrics
    assert metrics["imputed_sites"] == len(rows)
    assert 0 < metrics["low_confidence_sites"] < len(rows)
    concordance, sites = _concordance(output)
    assert sites > 500
    assert concordance >= 0.95

    # qc vcf tells the user the genotypes are imputed, not sequenced.
    stats = api.qc.vcf(api.QcVcfParams(vcf_input=output, outdir=tmp_path / "qc"))
    assert stats.metrics["imputed_by"] == "GLIMPSE2"
    assert "imputed_vcf" in {w.code for w in stats.warnings}


def _beagle_installed() -> bool:
    prefix = pixi_env_prefix("imputation")
    return bool(prefix and list(prefix.glob("share/beagle-*/beagle.jar")))


@needs_htslib
@needs_glimpse
@pytest.mark.skipif(not _beagle_installed(), reason="imputation env not installed")
def test_beagle_imputes_chip_genotypes_and_keeps_the_chip_calls_unmarked(
    reflib, tmp_path
):
    result = api.vcf.impute(
        api.VcfImputeParams(
            vcf_input=FIXTURE / "chip.vcf.gz",
            ref=reflib,
            outdir=tmp_path / "out",
            threads=2,
            memory="1G",
        )
    )

    (output,) = [Path(o.path) for o in result.outputs if o.role == "imputed_variants"]
    assert "##wgsextract_imputation=<Tool=Beagle" in _header(output)
    chip = {int(r[0]) for r in _query(FIXTURE / "chip.vcf.gz", "%POS\n")}
    rows = {int(pos): imp for pos, imp in _query(output, "%POS\t%INFO/IMP\n")}
    assert {p for p, imp in rows.items() if imp != "1"} == chip
    assert len(rows) > 10 * len(chip)
    concordance, sites = _concordance(output)
    assert sites > 300
    assert concordance >= 0.9


def test_impute_takes_reads_or_a_vcf_not_both(tmp_path):
    with pytest.raises(UsageError, match="either --input"):
        api.VcfImputeParams(
            input=FIXTURE / "reads.bam", vcf_input=FIXTURE / "chip.vcf.gz"
        )


def test_ref_imputation_rejects_chromosomes_without_a_panel(tmp_path):
    with pytest.raises(api.CommandFailed, match="chrX is not supported"):
        api.ref.imputation(api.RefImputationParams(chromosomes="chrX", ref=tmp_path))


@needs_glimpse
def test_ref_imputation_checks_free_space_before_downloading(
    tmp_path, panel_downloads, monkeypatch
):
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage._replace(free=10**9))

    with pytest.raises(api.CommandFailed, match=r"needs about 1\.4 GB free"):
        api.ref.imputation(api.RefImputationParams(chromosomes="chr20", ref=tmp_path))
    assert panel_downloads == []


@needs_htslib
def test_impute_without_an_installed_panel_says_how_to_install_it(tmp_path):
    with pytest.raises(api.CommandFailed, match="ref imputation --install"):
        api.vcf.impute(
            api.VcfImputeParams(
                vcf_input=FIXTURE / "chip.vcf.gz", ref=tmp_path, outdir=tmp_path
            )
        )
