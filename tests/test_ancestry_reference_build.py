"""The maintainer-only reference builder uses explicitly configured assemblies."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BUILD = Path(__file__).resolve().parents[1] / "scripts/ancestry_reference/build.py"


@pytest.mark.parametrize(
    ("input_state", "error"),
    [
        ("no-config", "package requires --config"),
        ("no-fasta", "GRCh38 FASTA not found"),
        ("no-index", "index the FASTA with samtools faidx"),
    ],
)
def test_package_reports_missing_local_reference_inputs(
    tmp_path: Path, input_state: str, error: str
) -> None:
    args = [sys.executable, str(BUILD), "package", "--work", str(tmp_path)]
    if input_state != "no-config":
        config = tmp_path / "config.toml"
        config.write_text('[ancestry_reference.fasta]\nGRCh38 = "assembly.fa"\n')
        args.extend(["--config", str(config)])
    if input_state == "no-index":
        (tmp_path / "assembly.fa").write_text(">chr1\nA\n")
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert error in result.stderr
