"""Local, offline dependency errors for #1131's opt-in CUDA PairHMM."""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fake_tool_launchers import expose_tool_fakes
from tests.fake_tools import python_tool
from tests.htslib_helpers import require_runnable, sam_to_bam, write_fasta
from wgsextract_cli.api.vcf import VcfGatkParams
from wgsextract_cli.core import native_pairhmm, native_support
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.native_pairhmm import cuda_pairhmm_java_options
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.main import main


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


@pytest.fixture
def linux_x86(monkeypatch: pytest.MonkeyPatch) -> None:
    host = SimpleNamespace(platform="linux")
    arch = SimpleNamespace(machine=lambda: "x86_64")
    for module in (native_pairhmm, native_support):
        monkeypatch.setattr(module, "sys", host)
        monkeypatch.setattr(module, "platform", arch)


@pytest.fixture
def cuda_jar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linux_x86: None) -> Path:
    # Simulate a launcher-safe Linux resolution, but read the real test archive.
    # A resolved Windows path includes a drive colon and fails the earlier guard.
    monkeypatch.chdir(tmp_path)
    path = Path("cuda.jar")
    original_resolve = Path.resolve

    def resolve(candidate: Path, strict: bool = False) -> Path:
        if candidate == path:
            return path
        return original_resolve(candidate, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    return path


@pytest.mark.parametrize("kind", ["missing", "corrupt", "incomplete"])
def test_1131_missing_or_invalid_sidecar_errors_without_fetching(
    tmp_path: Path, cuda_jar: Path, kind: str
) -> None:
    if kind == "corrupt":
        cuda_jar.write_bytes(b"not a jar")
    elif kind == "incomplete":
        sidecar(cuda_jar, complete=False)
    with pytest.raises(UsageError, match="nothing is installed or downloaded"):
        cuda_pairhmm_java_options(cuda_jar, "4.6.2.0")
    assert sorted(p.name for p in tmp_path.iterdir()) == (
        [] if kind == "missing" else ["cuda.jar"]
    )


def test_1131_incompatible_gatk_is_rejected_before_native_execution(
    cuda_jar: Path,
) -> None:
    with pytest.raises(UsageError, match="requires GATK 4.6.2.0"):
        cuda_pairhmm_java_options(sidecar(cuda_jar), "4.7.0.0")


@pytest.mark.usefixtures("linux_x86")
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


def test_1131_native_cuda_and_neon_cannot_be_selected_together() -> None:
    with pytest.raises(UsageError, match="cannot be combined with --pair-hmm-neon"):
        VcfGatkParams(
            input=Path("reads.bam"),
            pair_hmm_cuda=Path("cuda.jar"),
            pair_hmm_neon=Path("neon.jar"),
        )


def test_1131_flow_aware_ultima_rejects_cuda_without_running_caller(
    tmp_path: Path,
    cuda_jar: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    require_runnable("samtools")
    bindir = tmp_path / "bin"
    started = tmp_path / "caller-started"
    python_tool(
        bindir,
        "gatk",
        "import sys\n"
        "from pathlib import Path\n"
        "if '--version' in sys.argv:\n"
        "    print('The Genome Analysis Toolkit v4.6.2.0')\n"
        "else:\n"
        f"    Path({str(started)!r}).touch()\n"
        "    sys.exit(1)\n",
    )
    expose_tool_fakes(monkeypatch, bindir, "gatk")
    clear_tool_caches()
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "A" * 200})
    bam = tmp_path / "ultima.bam"
    sam_to_bam(
        [
            "@HD\tVN:1.6\tSO:coordinate",
            "@SQ\tSN:chr1\tLN:200",
            "@RG\tID:rg\tSM:S\tPL:ULTIMA\tFO:TGCA",
            f"read\t0\tchr1\t1\t60\t100M\t*\t0\t0\t{'A' * 100}"
            f"\t{'I' * 100}\tRG:Z:rg\ttp:B:c{',0' * 100}\tt0:Z:{'I' * 100}",
        ],
        bam,
    )
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as done:
        main(
            [
                "vcf",
                "gatk",
                "--input",
                str(bam),
                "--ref",
                str(ref),
                "--outdir",
                str(out),
                "--pair-hmm-cuda",
                str(sidecar(cuda_jar)),
                "--json",
            ]
        )
    assert done.value.code != 0
    captured = capsys.readouterr()
    assert "flow likelihood model" in captured.err
    assert not started.exists()
    assert not (out / "gatk.vcf.gz").exists()
