"""Issue 629: publication smoke must not accept missing annotation sources."""

import os
import runpy
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from tests.htslib_helpers import needs_tools
from wgsextract_cli.core.dependencies import pixi_env_prefix

RECIPE = Path(__file__).resolve().parents[1] / "packaging" / "fastvep"
pytestmark = needs_tools("fastvep")


@pytest.fixture
def package_smoke(tmp_path: Path) -> Path:
    shutil.copytree(RECIPE, tmp_path / "recipe")
    return tmp_path


def test_issue_629_committed_package_fixtures_pass_smoke(
    package_smoke: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = pixi_env_prefix("fastvep")
    if prefix is None:
        pytest.skip("Owned fastVEP package environment is not installed")
    executable = (
        prefix / "Library" / "bin" / "fastvep.exe"
        if sys.platform == "win32"
        else prefix / "bin" / "fastvep"
    )
    assert executable.is_file()
    fastvep = str(executable.resolve())
    monkeypatch.setenv("PATH", str(Path(fastvep).parent), prepend=os.pathsep)
    result = subprocess.run(
        [sys.executable, str(package_smoke / "recipe" / "check_vcf.py")],
        cwd=package_smoke,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ACMG VCF smoke passed" in result.stdout
    assert "RepeatMasker repeat intervals (BP3)" in result.stderr

    validate = cast(
        Callable[[str], None],
        runpy.run_path(str(RECIPE / "check_vcf.py"))["check_annotation_sources"],
    )
    validate(result.stderr)
    # Removing a real, successfully built database must fail publication even
    # though fastVEP itself returns success and still emits ACMG classifications.
    (package_smoke / "out" / "sa" / "revel.osa2").unlink()
    result = subprocess.run(
        [
            fastvep,
            "annotate",
            "-i",
            "out/fixtures/in_vep.vcf",
            "-o",
            "out/missing-revel.vcf",
            "--gff3",
            "out/fixtures/genes.gff3",
            "--fasta",
            "out/fixtures/genome.fa",
            "--sa-dir",
            "out/sa",
            "--acmg",
            "--pick-allele",
            "--no-progress",
        ],
        cwd=package_smoke,
        capture_output=True,
        text=True,
        check=True,
    )
    assert (package_smoke / "out" / "missing-revel.vcf").is_file()
    with pytest.raises(SystemExit, match="REVEL"):
        validate(result.stderr)
