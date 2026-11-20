"""#1123: exact records and safe fallback for the qualified Windows encoder."""

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.htslib_helpers import needs_htslib
from tests.test_bam_commands import genome as genome
from tests.test_bam_commands import no_remote_refs as no_remote_refs
from wgsextract_cli import api
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.core.variant_files import get_file_version

pytestmark = needs_htslib


def _body(output: Path, reference: str) -> str:
    result = run_command(
        ["samtools", "view", "-T", reference, str(output)], capture_output=True
    )
    return str(result.stdout)


def test_1123_unusable_windows_cram_allocator_keeps_exact_records(
    genome: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_remote_refs: None,
) -> None:
    real = shutil.which("samtools")
    assert real
    tools = tmp_path / "tools"
    tools.mkdir()
    primary = tools / ("samtools.exe" if sys.platform == "win32" else "samtools")
    if sys.platform == "win32":
        shutil.copy2(real, primary)
    else:
        primary.symlink_to(real)
    (tools / "samtools-mimalloc.exe").write_bytes(b"not an executable")
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    clear_tool_caches()
    bodies = []
    for allocator in ("off", "auto"):
        directory = tmp_path / allocator
        result = api.bam.to_cram(
            api.BamToCramParams(
                input=genome["bam"],
                ref=genome["ref"],
                outdir=directory,
                threads=2,
                cram_version="3.1",
                mimalloc=allocator,
            )
        )
        assert result.metrics["samtools_allocator"] == "system"
        (output,) = directory.glob("*.cram")
        bodies.append(_body(output, genome["ref"]))
    assert bodies[0] == bodies[1] and "p0\t" in bodies[0]
    clear_tool_caches()


def test_1123_compatibility_cram_retains_system_allocator(
    genome: dict[str, Any], tmp_path: Path, no_remote_refs: None
) -> None:
    result = api.bam.to_cram(
        api.BamToCramParams(
            input=genome["bam"],
            ref=genome["ref"],
            outdir=tmp_path,
            threads=2,
            mimalloc="auto",
        )
    )
    (output,) = tmp_path.glob("*.cram")
    assert get_file_version(str(output)).startswith("CRAM version 3.0")
    assert result.metrics["samtools_allocator"] == "system"
    assert "p0\t" in _body(output, genome["ref"])


@pytest.mark.skipif(
    sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"},
    reason="qualified native Windows x64 allocator package",
)
def test_1123_native_windows_cram_allocator_preserves_all_records(
    genome: dict[str, Any], tmp_path: Path, no_remote_refs: None
) -> None:
    primary = shutil.which("samtools")
    assert primary and Path(primary).with_name("samtools-mimalloc.exe").is_file()
    bodies = []
    for allocator in ("off", "auto"):
        directory = tmp_path / allocator
        result = api.bam.to_cram(
            api.BamToCramParams(
                input=genome["bam"],
                ref=genome["ref"],
                outdir=directory,
                threads=2,
                cram_version="3.1",
                mimalloc=allocator,
            )
        )
        assert result.metrics["samtools_allocator"] == (
            "mimalloc" if allocator == "auto" else "system"
        )
        (output,) = directory.glob("*.cram")
        bodies.append(_body(output, genome["ref"]))
    assert bodies[0] == bodies[1] and "p0\t" in bodies[0]
