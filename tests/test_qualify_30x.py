"""Desktop qualification planning and durable evidence without genome downloads."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qualify_30x.ps1"
HELPERS = ROOT / "scripts" / "qualify_30x_helpers.ps1"
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 required")


@pytest.mark.parametrize("phase", ["all", "prepare", "slice", "full"])
def test_1024_desktop_dry_run_never_creates_workspace(
    tmp_path: Path, phase: str
) -> None:
    workspace = tmp_path / "space & apostrophe's"
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-Root",
            str(workspace),
            "-Phase",
            phase,
            "-Threads",
            "2",
            "-DryRun",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert not workspace.exists()
    assert "No sample data is uploaded" in result.stdout
    expected = {
        "prepare": "download_bam",
        "slice": "forced_missing_input",
        "full": "full_realign",
    }
    for selected, step in expected.items():
        assert (step in result.stdout) == (phase in ("all", selected))


def test_1024_desktop_evidence_survives_failure_and_resumes(tmp_path: Path) -> None:
    runner = ROOT / "tests" / "powershell" / "qualify_30x.ps1"
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-File",
            str(runner),
            "-Helpers",
            str(HELPERS),
            "-Root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
    steps = {step["step"]: step for step in summary["steps"]}
    assert steps["success"]["status"] == "complete"
    assert steps["failure"]["status"] == "failed"
    assert steps["failure"]["exit_code"] == 7
    assert steps["forced_missing_input"]["status"] == "complete"
    assert steps["forced_missing_input"]["exit_code"] == 3
    assert steps["vcf_stats"]["output_records"] == 42
    assert steps["bam_records"]["output_records"] == 21
    retry = steps["retry"]
    assert retry["status"] == "complete"
    assert retry["attempt_count"] == 2
    assert [attempt["exit_code"] for attempt in retry["attempts"]] == [8, 0]
    assert retry["seconds"] == pytest.approx(
        sum(attempt["seconds"] for attempt in retry["attempts"]), abs=0.001
    )
    assert retry["peak_tree_ws_gib"] == max(
        attempt["peak_tree_ws_gib"] for attempt in retry["attempts"]
    )
    assert retry["peak_tree_ws_gib"] == 3
    assert summary["machine"]["cpu"] == "OS boundary fixture"
    assert (tmp_path / "evidence.zip").is_file()
    with zipfile.ZipFile(tmp_path / "evidence.zip") as archive:
        assert any(name.endswith("summary.md") for name in archive.namelist())
        assert not any(name.endswith(".bam") for name in archive.namelist())
        failure_log = next(
            name
            for name in archive.namelist()
            if name.endswith("attempts/retry/0001/stdout.log")
        )
        assert b"first failure" in archive.read(failure_log)


def test_1114_relative_roots_follow_powershell_location(tmp_path: Path) -> None:
    target = tmp_path / "space & apostrophe's"
    target.mkdir()
    runner = tmp_path / "paths.ps1"
    runner.write_text(
        "param($Script, $Target)\n"
        "Set-Location -LiteralPath $Target\n"
        "& $Script -Root './run' -InstallDir './install' -DryRun\n"
    )
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-File", str(runner), str(SCRIPT), str(target)],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    assert str(target / "run") in result.stdout
    assert str(target / "install") in result.stdout
    assert not (target / "run").exists()


def test_1114_slice_and_full_execute_real_realign_snp_indel(tmp_path: Path) -> None:
    from tests.htslib_helpers import sam_to_bam, vcf_calls, write_fasta
    from wgsextract_cli.core.dependencies import get_tool_path

    if not all(get_tool_path(name) for name in ("bwa", "samtools", "bcftools")):
        pytest.skip("BWA and htslib tools required")
    if not shutil.which("pixi") or not (ROOT / ".pixi/envs/default").is_dir():
        pytest.skip("Installed default Pixi environment required")
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    rng = random.Random(1114)
    seqs = {
        name: "".join(rng.choices("ACGT", k=5000))
        for name in ("chr1", "chr22", "chrX", "chrY")
    }
    ref = write_fasta(fixture / "ref.fa", seqs)
    rows = ["@HD\tVN:1.6\tSO:unsorted", "@RG\tID:r\tSM:HG002\tPL:ILLUMINA"]
    rows += [f"@SQ\tSN:{name}\tLN:5000" for name in seqs]
    for chrom in ("chr1", "chr22"):
        bases = list(seqs[chrom])
        bases[999] = "C" if bases[999] != "C" else "A"
        for start in range(500, 4000, 10):
            for flag, pos, mate, size in (
                (99, start, start + 250, 400),
                (147, start + 250, start, -400),
            ):
                if pos <= 1999 < pos + 150:
                    before = 1999 - pos
                    read = "".join(bases[pos - 1 : 1998] + bases[2003 : pos + 154])
                    cigar = f"{before}M5D{150 - before}M"
                else:
                    read = "".join(bases[pos - 1 : pos + 149])
                    cigar = "150M"
                rows.append(
                    f"{chrom}_{start}\t{flag}\t{chrom}\t{pos}\t60\t{cigar}\t=\t{mate}\t{size}"
                    f"\t{read}\t{'I' * len(read)}\tRG:Z:r"
                )
    bam = sam_to_bam(rows, fixture / "HG002.bam")
    from wgsextract_cli.core.reference_processing import install_ploidy_files

    library = fixture / "reflib"
    library.mkdir()
    assert install_ploidy_files(str(library))
    run = tmp_path / "run"
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-File",
            str(ROOT / "tests/powershell/qualify_30x_smoke.ps1"),
            "-Helpers",
            str(HELPERS),
            "-Root",
            str(run),
            "-InstallDir",
            str(ROOT),
            "-Reference",
            str(ref),
            "-Bam",
            bam,
            "-ReferenceLibrary",
            str(library),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((run / "evidence/summary.json").read_text())
    steps = {step["step"]: step for step in summary["steps"]}
    for part in ("slice", "full"):
        assert steps[f"{part}_realign"]["status"] == "complete"
        for kind in ("snp", "indel"):
            assert steps[f"{part}_{kind}"]["status"] == "complete"
            vcf = next((run / part / kind).glob("*.vcf.gz"))
            assert vcf_calls(vcf)
    assert {
        row[0] for row in vcf_calls(next((run / "slice/snp").glob("*.vcf.gz")))
    } == {"chr22"}
    assert {row[0] for row in vcf_calls(next((run / "full/snp").glob("*.vcf.gz")))} == {
        "chr1",
        "chr22",
    }
