"""Shared fake genome and slow-tool fixtures for server job tests."""

import os
import shutil
import sys
from pathlib import Path

import pytest

from tests.fake_tools import python_tool
from tests.server_helpers import FAKE_DIR
from tests.smoke_utils import ensure_fake_data


@pytest.fixture
def fake_data() -> tuple[Path, Path]:
    if not shutil.which("samtools") or not shutil.which("bcftools"):
        pytest.skip("samtools/bcftools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    return FAKE_DIR / "fake.bam", FAKE_DIR / "fake_ref.fa"


SLOW_SAMTOOLS = """
import os, subprocess, sys, time
if sys.argv[1:2] == ["coverage"]:
    open(os.path.join({pids!r}, str(os.getpid())), "w").close()
    time.sleep(600)
sys.exit(subprocess.run([{real!r}, *sys.argv[1:]]).returncode)
"""


@pytest.fixture
def slow_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_data) -> Path:
    """`samtools coverage` hangs (a very slow tool); other subcommands are real.
    Returns the folder where each hanging call records its pid."""
    if sys.platform == "win32":
        pytest.skip(
            "Windows starts a bare 'samtools' with CreateProcess, which finds "
            "only .exe files, so a scripted fake can't replace it"
        )
    pids = tmp_path / "pids"
    pids.mkdir()
    real = shutil.which("samtools")
    source = SLOW_SAMTOOLS.format(pids=str(pids), real=real)
    bindir = python_tool(tmp_path / "bin", "samtools", source).parent
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return pids
