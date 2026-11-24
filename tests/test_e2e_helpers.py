import json
import sys
from pathlib import Path

import pytest

from tests import e2e_base
from tests.fake_tools import python_tool
from tests.home_isolation import isolate_home
from tests.htslib_helpers import needs_htslib
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.pixi_tools import PIXI_TOOL_ENVS
from wgsextract_cli.core.runtime import repo_root
from wgsextract_cli.core.utils import run_command


# Finding a tool in another environment sends pixi a multi-line `python -c`,
# which the Windows batch-file stand-in can't pass on.
@pytest.mark.skipif(sys.platform == "win32", reason="batch-file pixi stand-in")
def test_optional_tool_needs_its_pixi_environment_installed(fake_pixi, monkeypatch):
    env = PIXI_TOOL_ENVS["freebayes"]
    monkeypatch.setattr(sys, "prefix", str(fake_pixi.env("default")))
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "default")
    assert not e2e_base.optional_tool_installed("freebayes")  # nowhere

    # Pixi would install the environment on first use: not installed yet.
    prefix = fake_pixi.env(env, locked=("freebayes",), installed=False)
    python_tool(prefix / "bin", "freebayes", "")
    clear_tool_caches()
    assert not e2e_base.optional_tool_installed("freebayes")

    fake_pixi.install(env)
    clear_tool_caches()
    assert e2e_base.optional_tool_installed("freebayes")


def test_optional_tool_on_the_host_is_installed(fake_pixi):
    python_tool(fake_pixi.host_bin, "freebayes", "")
    assert e2e_base.optional_tool_installed("freebayes")


@needs_htslib
def test_fake_data_generation_keeps_its_run_record_out_of_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#979 (fake-data generation wrote state/ into the checkout): the e2e
    suites isolate the home without a config, so the generating run fell back
    to the checkout's state/."""
    # Imported here: a top-level import puts this file in the serialized
    # shared-smoke group (tests/conftest.py), though it uses its own folder.
    from tests.smoke_utils import ensure_fake_data

    isolate_home(monkeypatch, tmp_path / "home")
    fake = tmp_path / "fake"
    ensure_fake_data(str(fake), coverage=0.02)

    assert (fake / "fake.bam.bai").is_file()
    record = repo_root() / "state" / "last-run.json"
    if record.is_file():
        outputs = json.loads(record.read_text())["result"]["outputs"]
        assert not any(
            Path(o["path"]).resolve().is_relative_to(fake.resolve()) for o in outputs
        )


@needs_htslib
@pytest.mark.parametrize("missing", ["cram", "crai", "indexes"])
def test_1512_completes_seeded_fake_data_without_rewriting_inputs(
    tmp_path: Path, missing: str
) -> None:
    # This fixture is private to the test, not in the shared-smoke group.
    from tests.smoke_utils import ensure_fake_data, run_cli

    fake = tmp_path / "fake"
    types = "bam,vcf" if missing == "cram" else "bam,cram,vcf"
    rc, _, stderr = run_cli(
        [
            "qc",
            "fake-data",
            "--outdir",
            str(fake),
            "--coverage",
            "0.02",
            "--type",
            types,
        ]
    )
    assert rc == 0, stderr
    cram = fake / "fake.cram"
    crai = fake / "fake.cram.crai"
    if missing == "cram":
        assert not cram.exists()
    else:
        crai.unlink()
    if missing == "indexes":
        (fake / "fake.bam.bai").unlink()
        (fake / "fake.vcf.gz.tbi").unlink()
    original = {
        path: path.stat().st_mtime_ns for path in fake.iterdir() if path.is_file()
    }

    ensure_fake_data(str(fake))

    assert all(path.stat().st_mtime_ns == mtime for path, mtime in original.items())
    assert crai.is_file()
    reads = run_command(
        ["samtools", "view", "-c", "-T", fake / "fake_ref.fa", cram, "chr1"],
        capture_output=True,
    )
    bam_reads = run_command(
        ["samtools", "view", "-c", fake / "fake.bam", "chr1"], capture_output=True
    )
    assert int(reads.stdout) == int(bam_reads.stdout) > 0
    assert run_command(
        ["bcftools", "view", "-H", "-r", "chr1", fake / "fake.vcf.gz"],
        capture_output=True,
    ).stdout
