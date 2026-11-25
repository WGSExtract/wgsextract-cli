"""Run the real routing CLI against the same checkout as its Git diff."""

import shutil
import subprocess
import sys
from pathlib import Path

from scripts.ci_test_dependencies import ROOT


def stage_router(root: Path) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    for name in (
        "ci_certifications",
        "ci_changes",
        "ci_moves",
        "ci_optional_tools",
        "ci_select_tests",
        "ci_test_dependencies",
        "ci_test_tiers",
    ):
        path = f"{name}.py"
        shutil.copyfile(ROOT / "scripts" / path, scripts / path)
    return scripts / "ci_changes.py"


def run_router(root: Path, base: str, *args: str) -> str:
    script = stage_router(root)
    return subprocess.check_output(
        [
            sys.executable,
            str(script),
            "--base",
            base,
            "--head",
            "HEAD",
            *args,
        ],
        cwd=root,
        text=True,
    )
