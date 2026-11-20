"""Offline dependency and mode errors for #1117's opt-in NEON PairHMM."""

import json
import sys
import zipfile
from pathlib import Path

import pytest

from wgsextract_cli.api.vcf import VcfGatkParams
from wgsextract_cli.core.native_pairhmm import neon_pairhmm_command
from wgsextract_cli.core.utils import UsageError


@pytest.fixture
def apple_silicon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wgsextract_cli.core.native_pairhmm.sys.platform", "darwin")
    monkeypatch.setattr(
        "wgsextract_cli.core.native_pairhmm.platform.machine", lambda: "arm64"
    )


def sidecar(tmp_path: Path, *, complete: bool = True, **metadata: object) -> Path:
    gatk = tmp_path / "gatk.jar"
    with zipfile.ZipFile(gatk, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "Implementation-Version: 4.6.2.0\n")
    path = tmp_path / "neon.jar"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "META-INF/wgse-pairhmm.json",
            json.dumps(
                {
                    "schema": 1,
                    "gatk": "4.6.2.0",
                    "gkl": "0.8.11",
                    "backend": "neon",
                    "java": sys.executable,
                    "gatk_jar": str(gatk),
                    **metadata,
                }
            ),
        )
        if complete:
            for name in ("utils", "pairhmm", "pairhmm_omp"):
                archive.writestr(
                    f"com/intel/gkl/native/darwin-aarch64/libgkl_{name}.dylib",
                    b"fixture",
                )
            for name in (
                "NativeLibraryLoader",
                "IntelGKLUtils",
                "pairhmm/IntelPairHmm",
                "pairhmm/IntelPairHmmOMP",
            ):
                archive.writestr(f"com/intel/gkl/{name}.class", b"fixture")
    return path


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("kind", ["missing", "corrupt", "incomplete", "metadata"])
def test_1117_invalid_native_input_errors_without_fetching(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "neon.jar"
    if kind == "corrupt":
        path.write_bytes(b"not a jar")
    elif kind == "incomplete":
        sidecar(tmp_path, complete=False)
    elif kind == "metadata":
        sidecar(tmp_path, backend="cuda")
    before = sorted(tmp_path.iterdir())
    with pytest.raises(UsageError, match="nothing is installed or downloaded"):
        neon_pairhmm_command(path, "4.6.2.0")
    assert sorted(tmp_path.iterdir()) == before


@pytest.mark.usefixtures("apple_silicon")
def test_1117_wrong_gatk_cannot_silently_run_an_incompatible_binding(
    tmp_path: Path,
) -> None:
    with pytest.raises(UsageError, match="requires GATK 4.6.2.0"):
        neon_pairhmm_command(sidecar(tmp_path), "4.7.0.0")


@pytest.mark.usefixtures("apple_silicon")
@pytest.mark.parametrize("key", ["java", "gatk_jar"])
def test_1117_moved_build_dependencies_ask_for_rebuild(
    tmp_path: Path, key: str
) -> None:
    missing = str(tmp_path / "no-longer-installed")
    path = (
        sidecar(tmp_path, java=missing)
        if key == "java"
        else sidecar(tmp_path, gatk_jar=missing)
    )
    with pytest.raises(UsageError, match="Rebuild the sidecar"):
        neon_pairhmm_command(path, "4.6.2.0")


def test_1117_neon_cannot_run_on_an_intel_mac(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wgsextract_cli.core.native_pairhmm.sys.platform", "darwin")
    monkeypatch.setattr(
        "wgsextract_cli.core.native_pairhmm.platform.machine", lambda: "x86_64"
    )
    with pytest.raises(UsageError, match="requires macOS Apple Silicon"):
        neon_pairhmm_command(tmp_path / "neon.jar", "4.6.2.0")


@pytest.mark.parametrize("mode", ["gpu", "scatter"])
def test_1117_opt_in_cannot_silently_choose_another_likelihood_engine(
    mode: str,
) -> None:
    with pytest.raises(UsageError, match="cannot be combined"):
        VcfGatkParams(
            input=Path("reads.bam"),
            pair_hmm_neon=Path("neon.jar"),
            gpu=mode == "gpu",
            scatter=mode == "scatter",
        )
