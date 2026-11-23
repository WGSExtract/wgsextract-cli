"""Allele counts and genotype denominators for each pair and N-way support."""

from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

from wgsextract_cli.core.table_writer import Column, Value

from .compare_records import Allele, Call

TYPES = ("all", "snv", "indel", "other_small")
_COUNTS = (
    "shared_alt",
    "a_only_observed",
    "b_only_observed",
    "a_only_callable",
    "b_only_callable",
    "a_only_unassessed",
    "b_only_unassessed",
    "gt_equal",
    "gt_compared",
    "ploidy_mismatch",
    "shared_ti",
    "shared_tv",
    "a_only_ti",
    "a_only_tv",
    "b_only_ti",
    "b_only_tv",
)
_RATIOS = ("gt_concordance", "shared_titv", "a_only_titv", "b_only_titv")
PAIR_COLUMNS = (
    Column("a", "TEXT"),
    Column("b", "TEXT"),
    Column("variant_type", "TEXT"),
    *(Column(name, "INTEGER") for name in _COUNTS),
    *(Column(name, "REAL") for name in _RATIOS),
)
SUPPORT_COLUMNS = (
    Column("variant_type", "TEXT"),
    Column("support", "INTEGER"),
    Column("alleles", "INTEGER"),
)


def variant_type(allele: Allele) -> str:
    _, _, ref, alt = allele
    if len(ref) != len(alt):
        return "indel"
    if len(ref) == 1 and {ref, alt} <= set("ACGT") and ref != alt:
        return "snv"
    return "other_small"


def _ratio(a: int, b: int) -> float | None:
    return a / b if b else None


@dataclass
class Summary:
    labels: tuple[str, ...]
    support: Counter[tuple[str, int]] = field(default_factory=Counter)
    states: Counter[tuple[int, str]] = field(default_factory=Counter)
    pairs: dict[tuple[int, int, str], Counter[str]] = field(init=False)

    def __post_init__(self) -> None:
        self.pairs = {
            (a, b, kind): Counter()
            for a, b in combinations(range(len(self.labels)), 2)
            for kind in TYPES
        }

    def add(self, allele: Allele, kind: str, calls: list[Call]) -> None:
        total = sum(call.carried for call in calls)
        ti = frozenset(allele[2:]) in {frozenset("AG"), frozenset("CT")}
        for i, call in enumerate(calls):
            self.states[i, call.state] += 1
        for subset in ("all", kind):
            self.support[subset, total] += 1
            for a, b in combinations(range(len(calls)), 2):
                first, second = calls[a], calls[b]
                if not (first.carried or second.carried):
                    continue
                counter = self.pairs[a, b, subset]
                if first.carried and second.carried:
                    side = "shared"
                    counter["shared_alt"] += 1
                    if first.state == second.state == "carried":
                        if first.ploidy != second.ploidy:
                            counter["ploidy_mismatch"] += 1
                        else:
                            counter["gt_compared"] += 1
                            counter["gt_equal"] += first.dosage == second.dosage
                else:
                    side, missing = (
                        ("a_only", second) if first.carried else ("b_only", first)
                    )
                    counter[f"{side}_observed"] += 1
                    if missing.state == "callable_reference":
                        counter[f"{side}_callable"] += 1
                    if missing.state in {"absent", "not_assessed"}:
                        counter[f"{side}_unassessed"] += 1
                if kind == "snv":
                    counter[f"{side}_{'ti' if ti else 'tv'}"] += 1

    def pair_rows(self) -> list[list[Value]]:
        return [
            [
                self.labels[a],
                self.labels[b],
                kind,
                *(c[name] for name in _COUNTS),
                _ratio(c["gt_equal"], c["gt_compared"]),
                *(
                    _ratio(c[f"{side}_ti"], c[f"{side}_tv"])
                    for side in ("shared", "a_only", "b_only")
                ),
            ]
            for (a, b, kind), c in self.pairs.items()
        ]

    def support_rows(self) -> list[list[Value]]:
        return [
            [kind, support, self.support[kind, support]]
            for kind in TYPES
            for support in range(len(self.labels) + 1)
            if self.support[kind, support]
        ]

    def label_states(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {label: {} for label in self.labels}
        for (index, state), count in sorted(self.states.items()):
            counts[self.labels[index]][state] = count
        return counts
