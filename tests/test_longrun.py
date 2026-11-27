"""Regression coverage for #1636's interrupted runs and mismatched references."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform not in {"linux", "darwin"}, reason="POSIX benchmark driver"
)


@pytest.fixture
def coord(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    stub = tmp_path / "agent-coord"
    stub.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$0.calls"\n', encoding="utf-8"
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return tmp_path / "agent-coord.calls"


def _driver(root: Path, body: str, *args: str) -> subprocess.CompletedProcess[str]:
    source = (
        "import sys\nfrom pathlib import Path\nfrom scripts.longrun import LongRun\n"
        "root = Path(sys.argv[1])\n"
        "with LongRun(root, name='bench', target='supervisor') as run:\n"
        f"{body}"
    )
    return subprocess.run(
        [sys.executable, "-c", source, str(root), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_1636_failed_stage_alerts_and_resume_does_not_repeat_passed_work(
    tmp_path: Path, coord: Path
) -> None:
    root = tmp_path / "run"
    body = """\
    run.hold('--io')
    run.stage('slice', ['bash', '-c', 'echo once >> "$1"', '_', str(root / 'count')])
    run.require_slice(root)
    run.stage('full', ['bash', '-c', 'echo worker-log; exit "$1"', '_', sys.argv[2]])
"""
    failed = _driver(root, body, "7")
    assert failed.returncode == 7, failed.stderr
    assert not (root / "full" / "PASS").exists()
    log = root / "full" / "stage.log"
    alerts = coord.read_text(encoding="utf-8")
    assert "--as bench send --to supervisor bench FAIL host=" in alerts
    assert f"step=full exit=7 log={log}" in alerts
    assert f"step=full log={log}" in (root / "driver.log").read_text(encoding="utf-8")
    assert "FAIL exit=7" in (root / "handoff.md").read_text(encoding="utf-8")
    assert "worker-log" in log.read_text(encoding="utf-8")

    resumed = _driver(root, body, "0")
    assert resumed.returncode == 0, resumed.stderr
    assert (root / "count").read_text(encoding="utf-8").splitlines() == ["once"]
    assert (root / "full" / "PASS").is_file()
    stats = json.loads((root / "full" / "metrics.json").read_text(encoding="utf-8"))
    assert (
        stats["exit_code"] == 0
        and stats["wall_seconds"] > 0
        and stats["peak_rss_kib"] > 0
    )
    assert "bench PASS host=" in coord.read_text(encoding="utf-8")
    assert not (root / "driver.pid").exists()


def test_1636_bad_pin_refuses_to_reuse_pass_marker(tmp_path: Path, coord: Path) -> None:
    root, source = tmp_path / "run", tmp_path / "source"
    source.write_bytes(b"original")
    pin = hashlib.sha256(source.read_bytes()).hexdigest()
    body = """\
    run.check_sha256(Path(sys.argv[2]), sys.argv[3])
    run.stage('benchmark', ['true'])
"""
    assert _driver(root, body, str(source), pin).returncode == 0
    source.write_bytes(b"changed!")
    refused = _driver(root, body, str(source), pin)
    assert refused.returncode != 0
    assert "SHA-256 mismatch" in (root / "driver.log").read_text(encoding="utf-8")
    assert "step=preflight exit=65" in coord.read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("samtools"), reason="samtools required")
def test_1636_cram_identity_refuses_wrong_reference_before_staging(
    tmp_path: Path, coord: Path
) -> None:
    root, reference, cram = (
        tmp_path / "run",
        tmp_path / "reference.fa",
        tmp_path / "reads.cram",
    )
    reference.write_text(">chr1\nACGTACGT\n", encoding="utf-8")
    sam = "@HD\tVN:1.6\n@SQ\tSN:chr1\tLN:8\nr1\t0\tchr1\t1\t60\t8M\t*\t0\t0\tACGTACGT\tIIIIIIII\n"
    subprocess.run(
        ["samtools", "view", "-C", "-T", str(reference), "-o", str(cram), "-"],
        input=sam,
        text=True,
        check=True,
        timeout=10,
    )
    body = """\
    run.check_reference(Path(sys.argv[2]), Path(sys.argv[3]), dictionary=(sys.argv[4] == 'dict'))
    run.stage('staging', ['true'])
"""
    wrong = tmp_path / "wrong.fa"
    wrong.write_text(">chr1\nTCGTACGT\n", encoding="utf-8")
    refused = _driver(root, body, str(cram), str(wrong), "fasta")
    assert refused.returncode != 0
    assert "Reference M5 mismatch for chr1" in refused.stderr
    assert not (root / "staging").exists()
    dictionary = tmp_path / "ref.dict"
    dictionary.write_bytes(
        subprocess.check_output(["samtools", "dict", str(reference)], timeout=10)
    )
    assert _driver(root, body, str(cram), str(dictionary), "dict").returncode == 0


def test_1636_duplicate_driver_cannot_replace_handoff(
    tmp_path: Path, coord: Path
) -> None:
    root = tmp_path / "run"
    completed = _driver(
        root,
        """\
    run.stage('nested', [sys.executable, '-c', sys.argv[2], str(root)])
""",
        "import sys; from pathlib import Path; from scripts.longrun import LongRun\n"
        "with LongRun(Path(sys.argv[1]), name='racing', target='supervisor'): pass\n",
    )
    assert completed.returncode == 73
    assert "Another driver holds the lock" in (root / "nested" / "stage.log").read_text(
        encoding="utf-8"
    )
    assert (root / "handoff.md").read_text(encoding="utf-8").startswith("# bench\n")


def test_1636_unwritable_run_path_alerts_before_any_stage(
    tmp_path: Path, coord: Path
) -> None:
    occupied = tmp_path / "not-a-directory"
    occupied.touch()
    result = _driver(occupied, "    run.stage('benchmark', ['true'])\n")
    assert result.returncode != 0
    assert "step=preflight exit=1" in coord.read_text(encoding="utf-8")


def test_1636_sigterm_stops_worker_and_leaves_stage_retryable(
    tmp_path: Path, coord: Path
) -> None:
    root = tmp_path / "run"
    interrupted = _driver(
        root,
        """\
    run.stage('benchmark', ['/bin/bash', '-c', 'echo "$$"; kill -TERM "$PPID"; exec sleep 30'])
""",
    )
    assert interrupted.returncode == 143
    assert not (root / "benchmark" / "PASS").exists()
    lines = (root / "benchmark" / "stage.log").read_text(encoding="utf-8").splitlines()
    with pytest.raises(ProcessLookupError):
        os.kill(int(lines[-1]), 0)
    assert "step=benchmark exit=143" in coord.read_text(encoding="utf-8")
    assert _driver(root, "    run.stage('benchmark', ['true'])\n").returncode == 0
