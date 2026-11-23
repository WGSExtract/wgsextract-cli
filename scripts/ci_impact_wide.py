"""Sample high-fan-out function callers for the advisory shadow comparison."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from scripts.ci_impact_scopes import ChangedScopes


@dataclass(frozen=True)
class CallerSample:
    selected: frozenset[str]
    group_sizes: dict[str, int]
    sampled: bool


def command_groups(
    observed_modules: Mapping[str, Iterable[str]],
) -> dict[str, set[str]]:
    """Use the command packages each test actually reached."""
    prefix = "src/wgsextract_cli/commands/"
    groups: dict[str, set[str]] = defaultdict(set)
    for path, tests in observed_modules.items():
        if path.startswith(prefix):
            group = path[len(prefix) :].split("/", 1)[0].removesuffix(".py")
            if group and not group.startswith("_"):
                for test in tests:
                    groups[test].add(group)
    return dict(groups)


def sample_callers(
    path: str,
    function: str,
    callers: set[str],
    observed_tests: set[str],
    groups: Mapping[str, set[str]],
    contracts: set[str],
    *,
    change: ChangedScopes,
    existed: bool,
    min_percent: int = 20,
    per_group: int = 3,
) -> CallerSample:
    """Preserve exclusive evidence and replace only body-only observed edges."""
    if not 0 < min_percent < 100 or per_group < 1:
        raise ValueError("invalid caller sampling threshold or group size")
    if (
        change.initialization
        or function not in change.functions
        or not existed
        or not callers <= observed_tests
        or len(callers) * 100 <= len(observed_tests) * min_percent
    ):
        return CallerSample(frozenset(callers), {}, False)

    strata: dict[str, set[str]] = defaultdict(set)
    unknown = set()
    for test in callers:
        if test_groups := groups.get(test):
            for group in test_groups:
                strata[group].add(test)
        else:
            unknown.add(test)
    # Do not narrow a caller that has no known command group.
    retained = set(contracts) | unknown
    for group, tests in strata.items():
        seed = f"{path}\0{function}\0{group}\0"
        ranked = sorted(
            tests, key=lambda test: hashlib.sha256((seed + test).encode()).digest()
        )
        retained.update(ranked[:per_group])
    return CallerSample(
        frozenset(retained),
        {group: len(tests) for group, tests in sorted(strata.items())},
        bool(callers - retained),
    )
