"""`vcf impute` and `ref imputation` on real HG002 data (#621).

tests/fixtures/imputation_hg002 (provenance.json) holds, for GRCh38
chr20:10,000,000-10,300,000: HG002 Illumina reads downsampled to about 1x, the
1000 Genomes 30x panel's common biallelic SNPs there (all 3,202 samples, GT
only), GLIMPSE's genetic map, GIAB v4.2.1's calls and confident regions, and a
23andMe v5-like chip VCF of HG002's truth genotypes at the chip's sites, with
contigs named 20 as chip exports name them. The panel and map stand in for the
pinned downloads; GLIMPSE2, Beagle and bcftools run for real.
"""

import csv
import gzip
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib
from wgsextract_cli import api
from wgsextract_cli.commands.vcf.imputation import beagle_command
from wgsextract_cli.core import dependencies, imputation_panel
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.pixi_tools import pixi_env_prefix
from wgsextract_cli.core.tool_catalog import (
    imputation_host_supported,
    platform_unavailable_tools,
)
from wgsextract_cli.core.utils import UsageError, WGSExtractError

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
        for pos, gt in _query(vcf, "%POS\t[%GT]\n", "-i", 'FILTER="PASS"')
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


needs_glimpse = pytest.mark.skipif(
    not imputation_host_supported()
    or any(
        not get_tool_path(tool)
        for tool in ("GLIMPSE2_phase", "GLIMPSE2_ligate", "GLIMPSE2_chunk")
    ),
    reason="supported imputation host and GLIMPSE2 tools required",
)


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
def test_glimpse2_imputes_1x_reads_and_labels_them(reflib, tmp_path, caplog):
    caplog.set_level("INFO")
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
    assert "Preflight impute" in caplog.text
    assert "reference bases" in caplog.text
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
@pytest.mark.parametrize(
    ("min_gp", "min_info", "threshold"),
    [(None, None, 0.95), (0.9, None, 0.9), (None, 0.8, 0.95)],
)
def test_beagle_preserves_hom_alt_pass_and_labels_filled_chip_no_calls(
    reflib, tmp_path, min_gp, min_info, threshold
):
    result = api.vcf.impute(
        api.VcfImputeParams(
            vcf_input=FIXTURE / "chip.vcf.gz",
            ref=reflib,
            outdir=tmp_path / "out",
            threads=2,
            memory="1G",
            min_info=min_info,
            min_gp=min_gp,
        )
    )

    (output,) = [Path(o.path) for o in result.outputs if o.role == "imputed_variants"]
    assert "##wgsextract_imputation=<Tool=Beagle" in _header(output)
    probes = _query(FIXTURE / "chip.vcf.gz", "%POS\t[%GT]\n")
    chip = {int(pos) for pos, gt in probes if "." not in gt}
    missing = {int(pos) for pos, gt in probes if "." in gt}
    rows = {int(pos): imp for pos, imp in _query(output, "%POS\t%INFO/IMP\n")}
    assert {p for p, imp in rows.items() if imp != "1"} == chip
    assert len(rows) > 10 * len(chip)
    panel_counts = {
        (pos, ref, alt): (ac, an)
        for pos, ref, alt, ac, an in _query(
            imputation_panel.require("chr20", str(reflib), None).panel,
            "%POS\t%REF\t%ALT\t%AC\t%AN\n",
        )
    }
    for pos, ref, alt, ac, an in _query(
        output, "%POS\t%REF\t%ALT\t%WGSE_PANEL_AC\t%WGSE_PANEL_AN\n"
    ):
        assert (ac, an) == panel_counts[pos, ref, alt]
    filters = _query(output, "%INFO/IMP\t%INFO/DR2\t[%GP]\t[%GT]\t%FILTER\n")
    confident_hom_alt = [
        filt
        for imp, dr2, gp, gt, filt in filters
        if imp == "1"
        and float(dr2) == 0
        and gt.replace("|", "/") == "1/1"
        and max(map(float, gp.split(","))) >= threshold
    ]
    assert confident_hom_alt
    # #732: a single target's DR2=0 must not turn confident hom-alts into no-calls.
    if min_info is None:
        assert all("ImpLowConf" not in filt for filt in confident_hom_alt)
    else:
        assert all("ImpLowConf" in filt for filt in confident_hom_alt)
    # #732: Beagle otherwise fills a ./., but leaves it unmarked as imputed.
    assert missing and all(rows[pos] == "1" for pos in missing)
    table = api.vcf.table(
        api.VcfTableParams(
            vcf_input=output,
            outdir=tmp_path / "table",
            all_alleles=True,
            no_sqlite=True,
        )
    )
    (tsv,) = [Path(o.path) for o in table.outputs if o.role == "variant_table"]
    with tsv.open() as stream:
        missing_rows = [
            r
            for r in csv.DictReader(stream, delimiter="\t")
            if int(r["pos"]) in missing
        ]
    assert missing_rows and all(
        r["imputed"] == "1" and "." not in r["genotype"] for r in missing_rows
    )
    concordance, sites = _concordance(output)
    # An explicit cohort-style site screen deliberately removes most single-target calls.
    assert sites > (300 if min_info is None else 100)
    assert concordance >= 0.9


def test_impute_takes_reads_or_a_vcf_not_both(tmp_path):
    with pytest.raises(UsageError, match="either --input"):
        api.VcfImputeParams(
            input=FIXTURE / "reads.bam", vcf_input=FIXTURE / "chip.vcf.gz"
        )


@needs_glimpse
def test_ref_imputation_rejects_chromosomes_without_a_panel(tmp_path):
    with pytest.raises(api.CommandFailed, match="chrX is not supported"):
        api.ref.imputation(api.RefImputationParams(chromosomes="chrX", ref=tmp_path))


@needs_glimpse
def test_ref_imputation_checks_free_space_before_downloading(
    tmp_path, panel_downloads, monkeypatch
):
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage._replace(free=10**9))

    with pytest.raises(api.CommandFailed, match=r"needs about 1\.7 GB free"):
        api.ref.imputation(api.RefImputationParams(chromosomes="chr20", ref=tmp_path))
    assert panel_downloads == []


@needs_htslib
@needs_glimpse
def test_impute_without_an_installed_panel_says_how_to_install_it(tmp_path):
    with pytest.raises(api.CommandFailed, match="ref imputation --install"):
        api.vcf.impute(
            api.VcfImputeParams(
                vcf_input=FIXTURE / "chip.vcf.gz", ref=tmp_path, outdir=tmp_path
            )
        )


@pytest.mark.parametrize(
    ("system", "machine"),
    [("win32", "ARM64"), ("darwin", "arm64"), ("linux", "aarch64"), ("linux", "arm64")],
)
@pytest.mark.parametrize("operation", ["impute", "install"])
def test_732_unsupported_platform_reports_gate_before_tools(
    tmp_path, monkeypatch, system, machine, operation
):
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    with pytest.raises(
        api.CommandFailed, match="x86-64 Linux, Windows and Intel macOS"
    ):
        if operation == "install":
            api.ref.imputation(
                api.RefImputationParams(ref=tmp_path, chromosomes="chr20")
            )
        else:
            api.vcf.impute(
                api.VcfImputeParams(
                    vcf_input=FIXTURE / "chip.vcf.gz", ref=tmp_path, outdir=tmp_path
                )
            )
    assert "GLIMPSE2_phase" in platform_unavailable_tools()


@pytest.mark.parametrize("operation", ["impute", "install"])
def test_621_windows_missing_tools_fail_validation_not_platform_gate(
    tmp_path, monkeypatch, operation
):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    # Tool discovery is the OS boundary; no external tools are installed here.
    monkeypatch.setattr(shutil, "which", lambda command: None)
    monkeypatch.setattr(dependencies, "get_tool_path", lambda tool: None)
    with pytest.raises(api.CommandFailed, match="Missing required .*tool"):
        if operation == "install":
            api.ref.imputation(
                api.RefImputationParams(ref=tmp_path, chromosomes="chr20")
            )
        else:
            api.vcf.impute(
                api.VcfImputeParams(
                    input=FIXTURE / "reads.bam", ref=tmp_path, outdir=tmp_path
                )
            )


def test_621_windows_beagle_requires_native_java_and_pinned_jar(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    jar = tmp_path / "share" / "beagle-5.5" / "beagle.jar"
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"not the pinned jar")
    with pytest.raises(WGSExtractError, match="Beagle is not installed"):
        beagle_command(tmp_path)
    java = tmp_path / "Library" / "bin" / "java.exe"
    java.parent.mkdir(parents=True)
    java.touch()
    with pytest.raises(WGSExtractError, match="not the pinned Beagle"):
        beagle_command(tmp_path)


@needs_glimpse
@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_732_imputation_never_overwrites_its_source(tmp_path, alias):
    source = tmp_path / "input.vcf.gz"
    source.write_bytes((FIXTURE / "chip.vcf.gz").read_bytes())
    output = tmp_path / "imputed.vcf.gz"
    if alias == "same":
        source.rename(output)
        source = output
    elif alias == "symlink":
        try:
            output.symlink_to(source)
        except OSError:
            pytest.skip("symlinks are unavailable")
    else:
        os.link(source, output)
    before = source.read_bytes()
    with pytest.raises(api.CommandFailed, match="overwrite its input"):
        api.vcf.impute(api.VcfImputeParams(vcf_input=source, outdir=tmp_path))
    assert source.read_bytes() == before


@needs_htslib
@needs_glimpse
@pytest.mark.skipif(not _beagle_installed(), reason="imputation env not installed")
def test_732_lengthless_chip_contig_explains_how_to_verify_build(reflib, tmp_path):
    chip = tmp_path / "lengthless.vcf"
    with gzip.open(FIXTURE / "chip.vcf.gz", "rt") as source:
        chip.write_text(re.sub(r",length=\d+", "", source.read()))
    with pytest.raises(api.CommandFailed, match="cannot verify GRCh38") as error:
        api.vcf.impute(
            api.VcfImputeParams(vcf_input=chip, ref=reflib, outdir=tmp_path, threads=2)
        )
    assert "bcftools reheader" in str(error.value)


@needs_htslib
@needs_glimpse
@pytest.mark.skipif(not _beagle_installed(), reason="imputation env not installed")
def test_732_reimputed_source_calls_keep_the_imputed_label(reflib, tmp_path):
    chip = tmp_path / "already-imputed.vcf"
    with gzip.open(FIXTURE / "chip.vcf.gz", "rt") as source:
        lines = source.read().splitlines()
    first = next(i for i, line in enumerate(lines) if not line.startswith("#"))
    fields = lines[first].split("\t")
    fields[7] = "IMP"
    tagged_pos = fields[1]
    lines[first] = "\t".join(fields)
    lines.insert(
        1, '##INFO=<ID=IMP,Number=0,Type=Flag,Description="Previously imputed">'
    )
    chip.write_text("\n".join(lines) + "\n")
    indexed = Path(bgzip_index(chip))
    result = api.vcf.impute(
        api.VcfImputeParams(
            vcf_input=indexed,
            ref=reflib,
            outdir=tmp_path / "out",
            threads=2,
            memory="1G",
        )
    )
    output = next(Path(o.path) for o in result.outputs if o.role == "imputed_variants")
    imputed = dict(_query(output, "%POS\t%INFO/IMP\n"))
    assert imputed[tagged_pos] == "1"


@needs_htslib
@needs_glimpse
@pytest.mark.parametrize("interruption", ["unfinished", "different-version"])
def test_732_panel_retry_checks_space_for_pending_binary_chunks(
    reflib, panel_downloads, monkeypatch, interruption
):
    marker = next(reflib.glob("imputation/*/chr20/.installed.json"))
    state = json.loads(marker.read_text())
    if interruption == "unfinished":
        state.pop("chunks")
    else:
        state["glimpse"] = "an older GLIMPSE2 build"
    marker.write_text(json.dumps(state))
    usage = shutil.disk_usage(reflib)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage._replace(free=1))
    fetched = len(panel_downloads)
    with pytest.raises(api.CommandFailed, match="needs about"):
        api.ref.imputation(
            api.RefImputationParams(ref=reflib, chromosomes="chr20", threads=2)
        )
    assert len(panel_downloads) == fetched
