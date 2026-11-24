import re
from pathlib import Path

import pytest
import tomllib


def assert_core_mypy_policy(root: Path) -> None:
    for name in ("mypy.ini", ".mypy.ini"):
        assert not (root / name).exists(), f"{name} would shadow pyproject.toml"

    with (root / "pyproject.toml").open("rb") as config:
        project = tomllib.load(config)
    settings = project.get("tool", {}).get("mypy", {})
    assert settings.get("strict") is True, "Keep [tool.mypy] strict = true"
    assert settings.get("files") == "src/wgsextract_cli", "Keep core in mypy's scope"
    minimum = project["project"]["requires-python"].removeprefix(">=")
    assert settings.get("python_version") == minimum, (
        "Check mypy's Python target against the minimum supported Python"
    )

    strict_flags = {
        "check_untyped_defs",
        "disallow_untyped_defs",
        "warn_return_any",
        "warn_unused_configs",
    }
    for option in strict_flags & settings.keys():
        assert settings[option] is True, f"Do not relax mypy's {option}"
    # New global options like exclude, disable_error_code, and follow_imports
    # can skip checks even in strict mode.
    new_options = (
        settings.keys()
        - strict_flags
        - {
            "strict",
            "files",
            "python_version",
            "ignore_missing_imports",
            "overrides",
        }
    )
    assert not new_options, f"Unreviewed global mypy options: {sorted(new_options)}"

    # Mypy can discard the entire config when override tables are malformed
    # or give conflicting values for the same module. Keep one table per module.
    overrides = settings.get("overrides", [])
    assert isinstance(overrides, list), "Use [[tool.mypy.overrides]] tables"
    seen: set[str] = set()
    core = "wgsextract_cli.core"
    for override in overrides:
        assert isinstance(override, dict), "Use [[tool.mypy.overrides]] tables"
        modules = override.get("module")
        assert isinstance(modules, (str, list)), "Name override modules"
        for module in [modules] if isinstance(modules, str) else modules:
            assert isinstance(module, str), "Name override modules as strings"
            assert module not in seen, f"Combine mypy overrides for {module}"
            seen.add(module)
            for pattern in module.replace("/", ".").replace("\\", ".").split(","):
                pattern = pattern.strip()
                # The wildcard suffix can match a core descendant if the
                # fixed prefix already matches core or an ancestor of core.
                prefix = pattern.split("*", 1)[0].rstrip(".")
                overlaps = prefix == core or prefix.startswith(core + ".")
                if "*" in pattern:
                    overlaps |= not prefix or core.startswith(prefix + ".")
                assert not overlaps, f"Core mypy override is not allowed: {pattern}"


def test_1508_core_mypy_exemption_baseline_is_empty() -> None:
    assert_core_mypy_policy(Path(__file__).resolve().parents[1])


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("strict = false", "strict = true"),
        ('disable_error_code = ["no-untyped-def"]', "disable_error_code"),
        ('python_version = "3.11"', "minimum supported Python"),
        ("overrides = {}", "[[tool.mypy.overrides]]"),
        (
            '[[tool.mypy.overrides]]\nmodule = "wgsextract_cli.commands"\nignore_errors = true\n'
            '[[tool.mypy.overrides]]\nmodule = "wgsextract_cli.commands"\nignore_errors = false',
            "Combine mypy overrides",
        ),
        (
            '[[tool.mypy.overrides]]\nmodule = "wgsextract_cli.core"\nignore_errors = true',
            "wgsextract_cli.core",
        ),
        (
            '[[tool.mypy.overrides]]\nmodule = ["wgsextract_cli.commands", "wgsextract_cli.*.config"]\ndisallow_untyped_defs = false',
            "wgsextract_cli.*.config",
        ),
        (
            '[[tool.mypy.overrides]]\nmodule = "wgsextract_cli.commands.*"\nignore_errors = true',
            None,
        ),
    ],
)
def test_1508_core_mypy_ratchet_rejects_weaker_config(
    tmp_path: Path, change: str, error: str | None
) -> None:
    strict = "" if change.startswith("strict =") else "strict = true\n"
    target = (
        "" if change.startswith("python_version =") else 'python_version = "3.10"\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.10"\n'
        f'[tool.mypy]\nfiles = "src/wgsextract_cli"\n{target}{strict}{change}\n'
    )
    if error is None:
        assert_core_mypy_policy(tmp_path)
    else:
        with pytest.raises(AssertionError, match=re.escape(error)):
            assert_core_mypy_policy(tmp_path)
