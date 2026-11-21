"""Offline validation and JVM-boundary tests for #1117's NEON PairHMM."""

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from tests.fake_tools import python_tool, require_bare_name_fakes
from tests.htslib_helpers import require_runnable, sam_to_bam, vcf_calls, write_fasta
from wgsextract_cli.api.vcf import VcfGatkParams
from wgsextract_cli.core.dependencies import clear_tool_caches
from wgsextract_cli.core.native_pairhmm import neon_pairhmm_command
from wgsextract_cli.core.utils import UsageError, run_command
from wgsextract_cli.main import main


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


@pytest.fixture
def neon_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    require_bare_name_fakes()
    require_runnable("samtools", "bcftools", "tabix")
    bindir = tmp_path / "bin"
    python_tool(bindir, "gatk", "print('The Genome Analysis Toolkit v4.6.2.0')")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    clear_tool_caches()
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "A" * 200})
    run_command(["samtools", "faidx", str(ref)])
    bam = tmp_path / "reads.bam"
    sam_to_bam(
        [
            "@HD\tVN:1.6\tSO:coordinate",
            "@SQ\tSN:chr1\tLN:200",
            "@RG\tID:rg\tSM:S\tPL:ILLUMINA",
            f"read\t0\tchr1\t1\t60\t100M\t*\t0\t0\t{'A' * 49}C{'A' * 50}"
            f"\t{'I' * 100}\tRG:Z:rg",
        ],
        bam,
    )
    return ref, bam


@pytest.mark.usefixtures("apple_silicon")
def test_1117_cli_keeps_double_precision_threads_and_neon_classpath(
    tmp_path: Path, neon_input: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    ref, bam = neon_input
    args_file = tmp_path / "jvm-args.json"
    java = python_tool(
        tmp_path / "jvm",
        "java",
        f"""\
import json, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
Path({str(args_file)!r}).write_text(json.dumps(args))
output = args[args.index("-O") + 1]
vcf = Path(output + ".vcf")
vcf.write_text('##fileformat=VCFv4.2\\n##contig=<ID=chr1,length=200>\\n'
               '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\\n'
               '#CHROM\\tPOS\\tID\\tREF\\tALT\\tQUAL\\tFILTER\\tINFO\\tFORMAT\\tS\\n'
               'chr1\\t50\\t.\\tA\\tC\\t60\\tPASS\\t.\\tGT\\t1/1\\n')
subprocess.run(["bcftools", "view", "-Oz", "-o", output, str(vcf)], check=True)
vcf.unlink()
""",
    )
    jar = sidecar(tmp_path, java=str(java))
    out = tmp_path / "out"
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
            "--pair-hmm-neon",
            str(jar),
            "--threads",
            "3",
            "--reference-evidence",
            "none",
            "--qc-min-depth",
            "0",
            "--json",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert vcf_calls(out / "gatk.vcf.gz") == [("chr1", "50", "A", "C", "1/1")]
    args = json.loads(args_file.read_text())
    # contract: double precision prevents the observed reference-block DP regression.
    assert args[args.index("--native-pair-hmm-use-double-precision") + 1] == "true"
    # contract: native PairHMM must honor the CLI's thread budget.
    assert args[args.index("--native-pair-hmm-threads") + 1] == "3"
    # contract: NEON's GKL overlay must precede GATK's bundled Java classes.
    assert args[args.index("-cp") + 1].split(os.pathsep) == [
        str(jar),
        str(tmp_path / "gatk.jar"),
    ]


@pytest.mark.usefixtures("apple_silicon")
def test_1117_invalid_sidecar_fails_before_cram_reindex(
    tmp_path: Path, neon_input: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    ref, bam = neon_input
    cram = tmp_path / "reads.cram"
    run_command(["samtools", "view", "-C", "-T", str(ref), "-o", str(cram), str(bam)])
    index = Path(str(cram) + ".crai")
    index.write_text("-1\t0\t100\t0\t0\t100\n")
    jar = tmp_path / "invalid.jar"
    jar.write_bytes(b"not a jar")
    with pytest.raises(SystemExit) as done:
        main(
            [
                "vcf",
                "gatk",
                "--input",
                str(cram),
                "--ref",
                str(ref),
                "--outdir",
                str(tmp_path / "out"),
                "--pair-hmm-neon",
                str(jar),
                "--json",
            ]
        )
    captured = capsys.readouterr()
    assert done.value.code != 0
    assert "sidecar" in captured.err
    assert "indexing the CRAM again" not in captured.err
    assert not (tmp_path / "ref.dict").exists()
    assert index.read_text() == "-1\t0\t100\t0\t0\t100\n"


@pytest.mark.usefixtures("apple_silicon")
def test_1117_flow_aware_ultima_rejected_before_input_preparation(
    tmp_path: Path, neon_input: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    ref, _bam = neon_input
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
    jar = sidecar(tmp_path)
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
                str(tmp_path / "out"),
                "--pair-hmm-neon",
                str(jar),
                "--json",
            ]
        )
    assert done.value.code != 0
    assert "flow likelihood model" in capsys.readouterr().err
    assert not (tmp_path / "ref.dict").exists()
