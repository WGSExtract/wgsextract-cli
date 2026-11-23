"""Probe package imports without making the shadow check a pytest gate."""

import subprocess
import sys
from pathlib import Path


def smoke_package(root: Path) -> str:
    source = root / "src"
    modules = []
    for path in (source / "wgsextract_cli").rglob("*.py"):
        parts = path.relative_to(source).with_suffix("").parts
        if parts[-1] == "__main__":
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules.append(".".join(parts))
    try:
        run = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from scripts.ci_check_moves import import_modules;"
                " import_modules(sys.argv[1:])",
                *sorted(modules, key=lambda module: (module.count("."), module)),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "timed out"
    if run.returncode:
        print(run.stderr)
        return "failed"
    return "passed"
