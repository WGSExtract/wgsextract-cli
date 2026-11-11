"""`qc ancestry` (#620): admixture-style proportions from reads, calls or a
microarray file, against the pinned ancestry reference."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import jsonschema
import numpy as np
import pytest

from tests.ancestry_helpers import build, calls_vcf, raw_23andme, reads_bam
from tests.htslib_helpers import needs_htslib
from wgsextract_cli import api
from wgsextract_cli.core import (
    ancestry,
    ancestry_reference,
    personal_report,
    sample_workspace,
)

TRUTH = [0.6, 0.3, 0.1, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    return build(tmp_path_factory.mktemp("ancestry"), TRUTH)


@pytest.fixture(autouse=True)
def pinned(sample, monkeypatch):
    # The test reference stands in for the published one.
    monkeypatch.setattr(ancestry_reference, "SHA256", sample["sha256"])


def _genome(tmp_path: Path) -> Path:
    folder = tmp_path / "genome"
    folder.mkdir()
    (folder / "genome-config.toml").write_text("")
    return folder


def _run(sample: dict, input: Path, **kwargs):
    params = api.QcAncestryParams(
        input=input,
        ref=kwargs.pop("ref", sample["fasta"]),
        build=kwargs.pop("build", "hg38"),
        bootstrap=kwargs.pop("bootstrap", 30),
        threads=2,
        **kwargs,
    )
    return api.qc.ancestry(params, check=False)


def _k7(table: Path) -> dict[str, tuple[float, float, float]]:
    with table.open() as f:
        return {
            row["component"]: (
                float(row["proportion"]),
                float(row["lower_95"]),
                float(row["upper_95"]),
            )
            for row in csv.DictReader(f, delimiter="\t")
            if row["k"] == "7"
        }


def _close_to_truth(k7: dict[str, tuple[float, float, float]], tolerance: float):
    for j, truth in enumerate(TRUTH):
        value, _low, _high = k7[f"Component {j + 1} of 7"]
        assert abs(value - truth) <= tolerance, (j, value, truth)


def test_projection_recovers_known_proportions_from_calls():
    rng = np.random.default_rng(1)
    p = rng.beta(0.5, 0.5, size=(20_000, 4))
    q = np.array([0.5, 0.25, 0.25, 0.0])
    g = rng.binomial(2, p @ q)
    result = ancestry.project(p, ancestry.hard_call_likelihoods(g), bootstrap=20)
    assert np.abs(result.proportions - q).max() < 0.02
    assert result.proportions.sum() == pytest.approx(1.0)
    assert (result.lower <= q + 0.02).all() and (q - 0.02 <= result.upper).all()


@needs_htslib
def test_bam_reads_give_the_proportions_and_record_a_fact(tmp_path, sample):
    folder = _genome(tmp_path)
    bam = reads_bam(sample, folder / "s.bam", depth=10)
    result = _run(sample, bam)
    assert result.exit_code == 0, result.error
    table = folder / "s.ancestry.tsv"
    k7 = _k7(table)
    _close_to_truth(k7, 0.05)
    value, low, high = k7["Component 1 of 7"]
    assert low <= TRUTH[0] <= high
    manifest = json.loads(Path(result.manifest).read_text())
    assert manifest["metrics"]["ancestry_sites"] == 3000
    assert manifest["metrics"]["ancestry_k7_component_1_of_7"] == pytest.approx(
        value, abs=1e-4
    )
    assert manifest["metrics"]["ancestry_depth"] == pytest.approx(10, abs=0.5)
    fact = sample_workspace.SampleWorkspace(folder).fact("ancestry", bam)
    assert fact is not None
    assert fact.value["Component 1 of 7"] == pytest.approx(value, abs=0.001)
    # Too few sites for a precise estimate: reported, with a warning.
    assert "ancestry_few_sites" in {w.code for w in result.warnings}

    # The personal report shows both views, worded as reference proportions.
    report = api.report.build(api.ReportParams(workspace=folder))
    section = report.details["sections"]["ancestry"]
    jsonschema.validate(report.details, personal_report.load_schema())
    shown = {(c["k"], c["name"]): c["proportion"] for c in section["components"]}
    assert shown[7, "Component 1 of 7"] == pytest.approx(value, abs=1e-4)
    assert {k for k, _ in shown} == {7, 12}
    html = next(folder.glob("*_personal_report.html")).read_text()
    assert "not ethnicities" in html and "Component 1 of 7" in html


@needs_htslib
def test_low_coverage_widens_the_intervals_without_bias(tmp_path, sample):
    deep = _run(sample, reads_bam(sample, tmp_path / "deep.bam", depth=10))
    shallow = _run(sample, reads_bam(sample, tmp_path / "shallow.bam", depth=2))
    k_deep = _k7(tmp_path / "deep.ancestry.tsv")
    k_shallow = _k7(tmp_path / "shallow.ancestry.tsv")
    assert deep.exit_code == shallow.exit_code == 0
    _close_to_truth(k_shallow, 0.1)
    name = "Component 1 of 7"
    assert (k_shallow[name][2] - k_shallow[name][1]) > (
        k_deep[name][2] - k_deep[name][1]
    )
    assert k_shallow[name][1] <= TRUTH[0] <= k_shallow[name][2]


@needs_htslib
def test_all_sites_vcf_and_microarray_file(tmp_path, sample):
    vcf = calls_vcf(sample, tmp_path / "s.vcf.gz", variants_only=False)
    result = _run(sample, vcf)
    assert result.exit_code == 0, result.error
    _close_to_truth(_k7(tmp_path / "s.ancestry.tsv"), 0.05)

    raw = raw_23andme(sample, tmp_path / "kit.txt")
    # The build of a microarray file is found from its positions.
    result = _run(sample, raw, build=None, ref=sample["reflib"])
    assert result.exit_code == 0, result.error
    assert result.metrics["ancestry_build"] == "hg38"
    _close_to_truth(_k7(tmp_path / "kit.ancestry.tsv"), 0.05)


@needs_htslib
def test_partial_variants_only_vcf_is_refused(tmp_path, sample):
    """A variants-only VCF of part of the genome: its missing sites can't be
    told hom-ref, and its variants alone would bias the proportions."""
    vcf = calls_vcf(sample, tmp_path / "v.vcf.gz", variants_only=True)
    result = _run(sample, vcf)
    assert result.exit_code == 1
    assert "lists only variants" in result.error.message
    assert not (tmp_path / "v.ancestry.tsv").exists()


@needs_htslib
def test_too_few_sites_are_refused(tmp_path, sample):
    bam = reads_bam(sample, tmp_path / "few.bam", depth=10, sites=1500)
    result = _run(sample, bam)
    assert result.exit_code == 1
    assert "1,500" in result.error.message
    assert not (tmp_path / "few.ancestry.tsv").exists()


@needs_htslib
def test_reference_not_matching_its_pin_is_refused(tmp_path, sample, monkeypatch):
    monkeypatch.setattr(ancestry_reference, "SHA256", "0" * 64)
    bam = reads_bam(sample, tmp_path / "s.bam", depth=2, sites=10)
    result = _run(sample, bam)
    assert result.exit_code == 1
    assert "SHA-256" in result.error.message
