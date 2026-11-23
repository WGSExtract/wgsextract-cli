"""High-fan-out helper shadow selection regressions (#1339)."""

from scripts.ci_impact_scopes import ChangedScopes, shadow_candidates
from scripts.ci_impact_wide import command_groups, sample_callers


def test_wide_body_edit_samples_each_command_group_and_retains_other_evidence() -> None:
    path = "src/wgsextract_cli/core/publisher.py"
    a = {f"tests/test_align_{i}.py" for i in range(10)}
    b = {f"tests/test_vcf_{i}.py" for i in range(10)}
    unknown = "tests/test_product_delegate.py"
    contract = "tests/test_publisher.py"
    mandatory = "tests/test_known_regression.py"
    callers = a | b | {unknown, contract}
    groups = command_groups(
        {
            "src/wgsextract_cli/commands/align/runner.py": a,
            "src/wgsextract_cli/commands/vcf/consensus.py": b,
            "src/wgsextract_cli/commands/_common.py": callers,
        }
    )
    change = ChangedScopes(False, frozenset({"publish"}))

    sample = sample_callers(
        path,
        "publish",
        callers,
        callers,
        groups,
        {contract},
        change=change,
        existed=True,
    )
    reversed_groups = dict(reversed(list(groups.items())))
    repeated = sample_callers(
        path,
        "publish",
        callers,
        callers,
        reversed_groups,
        {contract},
        change=change,
        existed=True,
    )
    result = shadow_candidates(
        callers | {mandatory},
        {},
        {path: change},
        callers | {mandatory},
        {},
        symbol_module_users={},
        widely_called_shared=sample.selected | {mandatory},
    )
    assert sample.selected == repeated.selected
    assert len(set(result["widely_called"]) & a) == 3
    assert len(set(result["widely_called"]) & b) == 3
    assert {unknown, contract, mandatory} <= set(result["widely_called"])
    assert set(result["symbol"]) == callers | {mandatory}


def test_wide_sampling_refuses_interface_edits_and_at_threshold() -> None:
    callers = {f"tests/test_{i}.py" for i in range(5)}
    observed = callers | {f"tests/test_other_{i}.py" for i in range(20)}
    groups = dict.fromkeys(callers, {"vcf"})
    for change, pool in (
        (ChangedScopes(True, frozenset({"run"})), callers),
        (ChangedScopes(False, frozenset({"run"})), observed),
    ):
        result = sample_callers(
            "src/runtime.py",
            "run",
            callers,
            pool,
            groups,
            set(),
            change=change,
            existed=True,
        )
        assert result.selected == callers
