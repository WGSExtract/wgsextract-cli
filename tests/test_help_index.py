"""Compact discovery caching across separate CLI invocations."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.home_isolation import isolated_home_env
from wgsextract_cli.cli.help_index import PACKAGE_INDEX

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("old_cache", [None, "stale", "broken", "incomplete"])
def test_1531_help_recovers_without_writing_package(
    tmp_path: Path, old_cache: str | None
) -> None:
    """Run from a disposable checkout with no built artifact."""
    import os
    import shutil

    source = tmp_path / "source"
    package = source / "wgsextract_cli"
    shutil.copytree(
        ROOT / "src/wgsextract_cli",
        package,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "help-index.json",
            "help-index.key",
        ),
    )
    env = {
        **os.environ,
        **isolated_home_env(tmp_path / "home"),
        "PYTHONPATH": str(source),
    }
    discovery = [sys.executable, "-m", "wgsextract_cli.main", "help", "--all", "--json"]
    cache_name = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from wgsextract_cli.cli.help_index import cache_path, registry_key; "
            "print(cache_path(registry_key()))",
        ],
        env=env,
        text=True,
    ).strip()
    cache_path = Path(cache_name)
    assert cache_path.is_relative_to(tmp_path)
    if old_cache == "incomplete":
        subprocess.check_output(discovery, env=env, cwd=tmp_path)
        record = json.loads(cache_path.read_bytes())
        record["index"]["commands"] = []
        cache_path.write_text(json.dumps(record), encoding="utf-8")
    elif old_cache is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            "{" if old_cache == "broken" else '{"key":"0.0.0-old","index":{}}',
            encoding="utf-8",
        )
    cold = subprocess.check_output(discovery, env=env, cwd=tmp_path)
    original = cache_path.read_bytes()
    warm = subprocess.check_output(discovery, env=env, cwd=tmp_path)
    assert warm == cold and cache_path.read_bytes() == original
    assert "vcf snp" in {item["path"] for item in json.loads(warm)["commands"]}
    assert not (package / PACKAGE_INDEX).exists()
    if old_cache is None:
        messages = package / "core" / "messages.py"
        with messages.open("a", encoding="utf-8") as stream:
            stream.write('\nCLI_HELP["arg_input"] = "Updated input help"\n')
        updated = subprocess.check_output(discovery, env=env, cwd=tmp_path)
        assert b"Updated input help" in updated
        assert cache_path.read_bytes() == original
        assert len(list(cache_path.parent.parent.glob("*/help-index.json"))) == 2
        # A command-summary edit must also invalidate the already-warm index.
        messages = package / "commands" / "_messages" / "vcf.py"
        with messages.open("a", encoding="utf-8") as stream:
            stream.write('\nCLI_HELP["cmd_snp"] = "Updated SNP summary"\n')
        updated = subprocess.check_output(discovery, env=env, cwd=tmp_path)
        commands = json.loads(updated)["commands"]
        assert next(
            item["summary"] for item in commands if item["path"] == "vcf snp"
        ) == ("Updated SNP summary")
