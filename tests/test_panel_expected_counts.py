"""Expected synonymous count matching in research panel calibration."""

from typing import Any

import pytest

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
