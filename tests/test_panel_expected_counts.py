"""Expected synonymous count matching in research panel calibration."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.htslib_helpers import needs_htslib
from tests.test_panel_spec import FIXTURES, _audit_inputs, _write
from wgsextract_cli import api
from wgsextract_cli.core.panel_calibration import Opportunity, matched_null


def test_1429_matching_expected_counts_can_change_the_underpowered_state() -> None:
    targets = [f"target_{i}" for i in range(10)]
    low = [f"low_{i}" for i in range(20)]
    high = [f"high_{i}" for i in range(20)]
    genes = targets + low + high
    opportunities = {
        gene: Opportunity("1", "autosomal", gene, 100, ((1, 100),), 100, 0)
        for gene in genes
    }
    counts = {**dict.fromkeys(targets, 1), **dict.fromkeys(high, 2)}
    expected = {**dict.fromkeys(targets + low, 5.0), **dict.fromkeys(high, 100.0)}

    def compare(covariate: dict[str, float] | None) -> dict[str, Any]:
        return matched_null(
            targets,
            set(targets),
            opportunities,
            counts,
            bins=2,
            null_sets=100,
            seed=1429,
            basis="callable_cds_bp",
            expected_syn=covariate,
        )

    baseline, joint = compare(None), compare(expected)
    assert baseline["state"] == joint["state"] == "calibrated"
    assert baseline["observed_allele_copies"] == joint["observed_allele_copies"] == 10
    assert baseline["underpowered"] is False and joint["underpowered"] is True
    assert joint["observed_expected_syn"] == joint["null_mean_expected_syn"] == 50
    assert joint["null_histogram"] == {0: 100}
    assert joint["upper_tail_p"] == pytest.approx(1 / 101)
    mismatched = compare({**expected, **dict.fromkeys(targets, 19.0)})
    assert mismatched["reason"] == "outside_control_expected_syn_range"
    assert "upper_tail_p" not in mismatched


@needs_htslib
def test_1429_gnomad_expected_counts_are_not_fetched_without_consent(
    tmp_path: Path,
) -> None:
    panel, vcf, _ = _audit_inputs(tmp_path, phase_set="1")
    library = tmp_path / "library"
    library.mkdir()
    exons = tmp_path / "model.tsv"
    exons.write_text(
        "#contig\tchr1\t248956422\n"
        "gene\ttranscript\ttag\tstrand\texon\texons\tchrom\tstart\tend\tcds_start\tcds_end\tlimitations\n"
        "MTHFR\tNM_005957.5\tMANE Select\t+\t1\t1\tchr1\t11796321\t11796330\t11796321\t11796330\t\n",
        encoding="utf-8",
    )
    result = api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=vcf,
            exons=exons,
            ref=library,
            research_panel=True,
            gnomad_expected=True,
            outdir=tmp_path / "protected",
        ),
        check=False,
    )
    assert result.exit_code == 2
    assert (
        result.error and "gnomad-v4.1-mane-expected-syn.tsv.gz" in result.error.message
    )
    assert "--download" in result.error.message
    assert not list(library.iterdir())


@needs_htslib
def test_1429_cached_expected_counts_compare_only_exact_transcript_matches(
    tmp_path: Path,
) -> None:
    panel, vcf, _ = _audit_inputs(tmp_path, phase_set="1")
    document = json.loads(panel.read_text(encoding="utf-8"))
    document["groups"][0]["genes"].append("AGRN")
    panel = _write(tmp_path, document, ".json")
    library = tmp_path / "library"
    cache = library / "panel"
    cache.mkdir(parents=True)
    asset = FIXTURES / "gnomad-v4.1-mane-expected-syn.tsv.gz"
    shutil.copyfile(asset, cache / asset.name)
    exons = tmp_path / "model.tsv"
    exons.write_text(
        "#contig\tchr1\t248956422\n"
        "gene\ttranscript\ttag\tstrand\texon\texons\tchrom\tstart\tend\tcds_start\tcds_end\tlimitations\n"
        + "".join(
            f"{name}\t{transcript}\tMANE Select\t+\t1\t1\tchr1\t{start}\t{start + 9}\t"
            f"{start}\t{start + 9}\t\n"
            for name, transcript, start in (
                ("MTHFR", "NM_005957.5", 11796321),
                ("AGRN", "NM_198576.9999", 1020125),
                ("SAMD11", "NM_001385641.1", 923930),
                ("PLEKHN1", "NM_032129.3", 966482),
            )
        ),
        encoding="utf-8",
    )

    def audit(expected: bool) -> dict[str, Any]:
        result = api.panel.audit(
            api.PanelAuditParams(
                panel=panel,
                vcf_input=vcf,
                exons=exons,
                ref=library,
                research_panel=True,
                null_sets=100,
                gnomad_expected=expected,
                outdir=tmp_path / ("paired" if expected else "cds-only"),
            )
        )
        report = next(o for o in result.outputs if o.role == "panel_audit")
        data = json.loads(Path(report.path).read_text(encoding="utf-8"))
        return data["groups"][0]["calibration"]

    baseline, augmented = audit(False), audit(True)
    comparison = augmented.pop("expected_count_comparison")
    assert augmented == baseline and baseline["target_genes"] == 2
    assert comparison["missing_target_expected_count"] == ["AGRN"]
    cds, joint = (
        comparison["cds_only_on_common_genes"],
        comparison["cds_and_expected_syn"],
    )
    assert cds["state"] == joint["state"] == "calibrated"
    assert cds["target_genes"] == joint["target_genes"] == 1
    assert cds["control_genes"] == joint["control_genes"] == 2
    assert (
        cds["observed_expected_syn"]
        == joint["observed_expected_syn"]
        == pytest.approx(353.39)
    )
