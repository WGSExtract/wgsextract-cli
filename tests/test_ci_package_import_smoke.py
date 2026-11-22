"""Catch package import errors in a fresh Linux interpreter (#1339)."""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_test_dependencies import ROOT


@pytest.mark.skipif(sys.platform != "linux", reason="Linux shadow lane")
def test_package_imports() -> None:
    source = ROOT / "src"
    modules = []
    for path in (source / "wgsextract_cli").rglob("*.py"):
        parts = path.relative_to(source).with_suffix("").parts
        if parts[-1] == "__main__":
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules.append(".".join(parts))
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from scripts.ci_check_moves import import_modules;"
            " import_modules(sys.argv[1:])",
            *sorted(modules, key=lambda module: (module.count("."), module)),
        ],
        cwd=Path(ROOT),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert run.returncode == 0, run.stderr
