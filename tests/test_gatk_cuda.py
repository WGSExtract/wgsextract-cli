"""Local, offline dependency errors for #1131's opt-in CUDA PairHMM."""

import json
import zipfile
from pathlib import Path

import pytest

from wgsextract_cli.api.vcf import VcfGatkParams
from wgsextract_cli.core.native_pairhmm import cuda_pairhmm_java_options
from wgsextract_cli.core.utils import UsageError


def sidecar(path: Path, *, complete: bool = True) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "META-INF/wgse-pairhmm.json",
            json.dumps(
                {
                    "schema": 1,
                    "gatk": "4.6.2.0",
                    "gkl": "0.8.11",
                    "backend": "cuda",
                }
            ),
        )
        if complete:
            for suffix in ("", "_omp"):
                archive.writestr(
                    f"com/intel/gkl/native/libgkl_pairhmm{suffix}.so", b"fixture"
                )
    return path


@pytest.fixture(autouse=True)
def linux_x86(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wgsextract_cli.core.native_pairhmm.sys.platform", "linux")
    monkeypatch.setattr(
        "wgsextract_cli.core.native_pairhmm.platform.machine", lambda: "x86_64"
    )


@pytest.mark.parametrize("kind", ["missing", "corrupt", "incomplete"])
def test_1131_missing_or_invalid_sidecar_errors_without_fetching(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "cuda.jar"
    if kind == "corrupt":
        path.write_bytes(b"not a jar")
    elif kind == "incomplete":
        sidecar(path, complete=False)
    with pytest.raises(UsageError, match="nothing is installed or downloaded"):
        cuda_pairhmm_java_options(path, "4.6.2.0")
    assert sorted(p.name for p in tmp_path.iterdir()) == (
        [] if kind == "missing" else ["cuda.jar"]
    )


def test_1131_incompatible_gatk_is_rejected_before_native_execution(
    tmp_path: Path,
) -> None:
    with pytest.raises(UsageError, match="requires GATK 4.6.2.0"):
        cuda_pairhmm_java_options(sidecar(tmp_path / "cuda.jar"), "4.7.0.0")


def test_1131_launcher_cannot_silently_drop_space_in_native_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(UsageError, match="without whitespace"):
        cuda_pairhmm_java_options(sidecar(tmp_path / "cuda pairhmm.jar"), "4.6.2.0")


@pytest.mark.parametrize("gpu,scatter", [(True, False), (False, True)])
def test_1131_native_cuda_cannot_silently_select_parabricks_or_scattering(
    gpu: bool, scatter: bool
) -> None:
    with pytest.raises(UsageError, match="cannot be combined"):
        VcfGatkParams(
            input=Path("reads.bam"),
            pair_hmm_cuda=Path("cuda.jar"),
            gpu=gpu,
            scatter=scatter,
        )
