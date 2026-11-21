"""#1123: exact records and safe fallback for the qualified Windows encoder."""

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.fake_tools import FakePixi, python_tool
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


def _fixture_executable(
    directory: Path, name: str, real: str, mode: str, state: Path | None = None
) -> Path:
    source = f"""
import json, os, subprocess, sys
mode = {mode!r}
state = {str(state) if state else None!r}
if state:
    mode = json.load(open(state))["mode"]
if sys.argv[1:] == ["--version"]:
    version = subprocess.run([{real!r}, "--version"], capture_output=True, text=True)
    print("samtools 0.1\\nUsing htslib 0.1" if mode == "mismatch" else version.stdout, end="")
    if mode != "missing_stats":
        print("subproc 0\\nprocess: peak commit: fixture", file=sys.stderr)
    sys.exit(7 if mode == "nonzero" else 0)
os.execv({real!r}, [{real!r}, *sys.argv[1:]])
"""
    return python_tool(directory, name, source)


def _convert(genome: dict[str, Any], directory: Path, mode: str) -> tuple[str, str]:
    result = api.bam.to_cram(
        api.BamToCramParams(
            input=genome["bam"],
            ref=genome["ref"],
            outdir=directory,
            threads=2,
            cram_version="3.1",
            mimalloc=mode,
        )
    )
    (output,) = directory.glob("*.cram")
    return str(result.metrics["samtools_allocator"]), _body(output, genome["ref"])


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="executable script fixtures; native Windows package tested below",
)
@pytest.mark.parametrize("rejection", ["mismatch", "nonzero", "missing_stats"])
def test_1195_rejected_probe_preserves_system_records(
    genome: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_remote_refs: None,
    rejection: str,
) -> None:
    real = shutil.which("samtools")
    assert real
    directory = tmp_path / "tools"
    directory.mkdir()
    (directory / "samtools").symlink_to(real)
    _fixture_executable(directory, "samtools-mimalloc.exe", real, rejection)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    clear_tool_caches()
    control = _convert(genome, tmp_path / "off", "off")
    rejected = _convert(genome, tmp_path / "auto", "auto")
    assert rejected == control
    assert rejected[0] == "system" and "p0\t" in rejected[1]


@pytest.mark.skipif(
    sys.platform == "win32", reason="executable script replacement fixtures"
)
@pytest.mark.parametrize("changed", ["samtools", "samtools-mimalloc.exe"])
def test_1195_changed_executable_invalidates_activation(
    genome: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_remote_refs: None,
    changed: str,
) -> None:
    real = shutil.which("samtools")
    assert real
    directory = tmp_path / "tools"
    directory.mkdir()
    for name, mode in (
        ("samtools", "missing_stats"),
        ("samtools-mimalloc.exe", "valid"),
    ):
        fixture = _fixture_executable(directory, name, real, mode)
        fixture.write_bytes(fixture.read_bytes().ljust(4096, b" "))
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    clear_tool_caches()
    allocator, body = _convert(genome, tmp_path / "first", "auto")
    assert allocator == "mimalloc"
    previous = (directory / changed).stat()
    replacement = _fixture_executable(
        tmp_path / "replacement", changed, real, "mismatch"
    )
    replacement.write_bytes(replacement.read_bytes().ljust(previous.st_size, b" "))
    os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    os.replace(replacement, directory / changed)
    allocator, changed_body = _convert(genome, tmp_path / "changed", "auto")
    assert allocator == "system" and changed_body == body


@pytest.mark.skipif(sys.platform == "win32", reason="executable script fixture state")
def test_1195_tool_cache_reset_clears_activation(
    genome: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_remote_refs: None,
) -> None:
    real = shutil.which("samtools")
    assert real
    directory = tmp_path / "tools"
    directory.mkdir()
    (directory / "samtools").symlink_to(real)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"mode": "valid"}))
    _fixture_executable(directory, "samtools-mimalloc.exe", real, "valid", state)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    clear_tool_caches()
    allocator, body = _convert(genome, tmp_path / "first", "auto")
    assert allocator == "mimalloc"
    state.write_text(json.dumps({"mode": "missing_stats"}))
    clear_tool_caches()
    allocator, changed_body = _convert(genome, tmp_path / "reset", "auto")
    assert allocator == "system" and changed_body == body


@pytest.mark.skipif(sys.platform == "win32", reason="Pixi executable script fixture")
def test_1195_pixi_only_samtools_uses_its_companion_without_installing(
    genome: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_remote_refs: None,
) -> None:
    real = shutil.which("samtools")
    assert real
    manifest = Path(__file__).resolve().parents[1] / "pixi.toml"
    pixi = FakePixi(tmp_path / "pixi", manifest)
    prefix = pixi.env("default", locked=("samtools",))
    (prefix / "bin/samtools").symlink_to(real)
    _fixture_executable(prefix / "bin", "samtools-mimalloc.exe", real, "valid")
    marker = prefix / "conda-meta/pixi"
    installed = marker.stat().st_mtime_ns
    monkeypatch.setenv("PIXI_EXE", str(pixi.exe))
    monkeypatch.setenv("PATH", str(pixi.host_bin))
    monkeypatch.delenv("PIXI_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    clear_tool_caches()
    control = _convert(genome, tmp_path / "off", "off")
    allocator, body = _convert(genome, tmp_path / "auto", "auto")
    assert allocator == "mimalloc" and body == control[1]
    assert marker.stat().st_mtime_ns == installed


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
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
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
