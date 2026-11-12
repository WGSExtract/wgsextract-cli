"""#646: vcf snp/indel run bcftools with wgse-mimalloc on Apple Silicon."""

import logging
import os
import platform
import shutil
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib
from tests.test_contig_calling import multicontig_sample  # noqa: F401 (fixture)
from wgsextract_cli import api
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.mimalloc import LIBRARY
from wgsextract_cli.core.utils import run_command

Sample = tuple[str, str]


def _call(
    sample: Sample, outdir: Path, kind: str, mimalloc: str
) -> tuple[str, dict[str, object]]:
    bam, ref = sample
    params_type = api.VcfSnpParams if kind == "snp" else api.VcfIndelParams
    params = params_type(
        input=Path(bam), ref=Path(ref), outdir=outdir, threads=3,
        ploidy="GRCh38", mimalloc=mimalloc,
    )  # fmt: skip
    result = getattr(api.vcf, kind)(params)
    vcf = outdir / f"{kind}s.vcf.gz"
    body = run_command(["bcftools", "view", "-H", str(vcf)], capture_output=True)
    return body.stdout, result.metrics


def _apple_silicon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")


def _installed_library() -> Path | None:
    bcftools = shutil.which("bcftools")
    library = Path(bcftools).parent.parent / LIBRARY if bcftools else None
    return library if library and library.is_file() else None


@needs_htslib
def test_off_calls_with_the_system_allocator(
    multicontig_sample: Sample,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _apple_silicon(monkeypatch)
    caplog.set_level(logging.INFO)
    body, metrics = _call(multicontig_sample, tmp_path, "snp", "off")
    assert "chr20\t" in body
    assert metrics["bcftools_allocator"] == "system"
    assert "mimalloc off" in caplog.text


@needs_htslib
def test_a_library_bcftools_cannot_load_falls_back_with_the_same_records(
    multicontig_sample: Sample,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A wgse-mimalloc that bcftools doesn't load (protected binary, broken
    file) must not break calling: bcftools runs with the system allocator."""
    real = shutil.which("bcftools")
    assert real
    env = tmp_path / "env"
    (env / "bin").mkdir(parents=True)
    (env / "bin" / "bcftools").symlink_to(real)
    library = env / LIBRARY
    library.parent.mkdir(parents=True)
    library.write_bytes(b"not a Mach-O library")
    monkeypatch.setenv("PATH", f"{env / 'bin'}{os.pathsep}{os.environ['PATH']}")
    clear_tool_caches()
    _apple_silicon(monkeypatch)
    caplog.set_level(logging.INFO)

    off, _ = _call(multicontig_sample, tmp_path / "off", "indel", "off")
    auto, metrics = _call(multicontig_sample, tmp_path / "auto", "indel", "auto")
    assert auto == off and "chr20\t" in auto
    assert metrics["bcftools_allocator"] == "system"
    assert f"didn't load {library}" in caplog.text
    clear_tool_caches()


@needs_htslib
@pytest.mark.skipif(
    not (platform.system() == "Darwin" and platform.machine() == "arm64")
    or _installed_library() is None,
    reason="wgse-mimalloc is installed only on macOS arm64",
)
@pytest.mark.parametrize("kind", ["snp", "indel"])
def test_mimalloc_calls_the_same_records(
    multicontig_sample: Sample,  # noqa: F811
    tmp_path: Path,
    kind: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    system, _ = _call(multicontig_sample, tmp_path / "system", kind, "off")
    mimalloc, metrics = _call(multicontig_sample, tmp_path / "mimalloc", kind, "auto")
    assert metrics["bcftools_allocator"] == "mimalloc"
    assert f"use mimalloc ({_installed_library()})" in caplog.text
    assert mimalloc == system and "chr20\t" in system
