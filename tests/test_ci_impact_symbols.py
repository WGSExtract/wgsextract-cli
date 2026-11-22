"""Structural symbol-selection regressions for #1339."""

import pytest

from scripts.ci_impact_scopes import changed_scopes, module_scopes, shadow_candidates
from scripts.ci_impact_symbols import symbol_delta


def test_eager_import_reordering_only_needs_import_smoke_and_contracts() -> None:
    old = b"import os\nfrom pathlib import Path\n\ndef resolve():\n    return Path.cwd()\n"
    new = b"from pathlib import Path\nimport os\n\ndef resolve():\n    return Path.cwd()\n"
    delta = symbol_delta(old, new)
    assert delta.imports
    assert not delta.broad
    assert not delta.symbols


def test_structural_deltas_report_signature_class_and_constant_symbols() -> None:
    old = b"""\
from dataclasses import dataclass
LIMIT = 2
@dataclass
class Result:
    count: int
def call(amount=2):
    return amount
"""
    new = (
        old.replace(b"LIMIT = 2", b"LIMIT = 3")
        .replace(b"    count: int", b"    count: int\n    ready: bool = True")
        .replace(b"amount=2", b"amount=3")
    )
    delta = symbol_delta(old, new)
    assert delta.symbols == {"LIMIT", "Result", "call"}
    assert not delta.broad


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"VALUE = 1", b"VALUE = register()"),
        (b"def run(): pass", b"@register\ndef run(): pass"),
        (b"__all__ = ['run']", b"__all__ = ['stop']"),
        (
            b"def __getattr__(name): return old",
            b"def __getattr__(name): return new",
        ),
        (b"class Item: pass", b"class Item(metaclass=Registry): pass"),
        (b"class Item: pass", b"class Item:\n    def __init_subclass__(cls): pass"),
        (b"", b"registry.register(run)"),
    ],
)
def test_import_time_side_effects_keep_the_module_scope(old: bytes, new: bytes) -> None:
    assert symbol_delta(old, new).broad


def test_package_initializer_import_still_reaches_all_package_users() -> None:
    assert symbol_delta(b"import os", b"import sys", package_init=True).broad


def test_third_shadow_variant_keeps_other_rules_and_missing_classification() -> None:
    ordinary = "src/wgsextract_cli/ordinary.py"
    dynamic = "src/wgsextract_cli/dynamic.py"
    tests = {
        "tests/test_direct.py",
        "tests/test_unrelated.py",
        "tests/test_consumers.py",
        "tests/test_uncertain.py",
    }
    result = shadow_candidates(
        ["tests/test_direct.py"],
        {
            ordinary: ["tests/test_unrelated.py", "tests/test_consumers.py"],
            dynamic: ["tests/test_uncertain.py"],
        },
        {
            ordinary: changed_scopes(
                module_scopes(b"LIMIT = 1"), module_scopes(b"LIMIT = 2")
            )
        },
        tests,
        {},
        symbol_module_users={ordinary: ["tests/test_consumers.py"]},
    )
    assert set(result["decisive"]) == set(result["tight"]) == tests
    assert set(result["symbol"]) == tests - {"tests/test_unrelated.py"}
