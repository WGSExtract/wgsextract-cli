"""The public record API rejects scientifically mismatched fact values."""

import subprocess
import sys
from pathlib import Path
from typing import get_args, get_type_hints

from wgsextract_cli.core.sample_document import DetectedFile
from wgsextract_cli.core.sample_types import FactName
from wgsextract_cli.core.sample_workspace import FACTS


def test_recordable_facts_cannot_drift_from_the_document_model():
    assert set(FACTS) == set(get_type_hints(DetectedFile)) - {"fingerprint"}
    assert set(FACTS) == set(get_args(FactName))


def test_mypy_rejects_mismatched_sample_fact_writes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = tmp_path / "mypy.ini"
    config.write_text(
        f"[mypy]\nstrict = True\nignore_missing_imports = True\n"
        f"mypy_path = {(root / 'src').as_posix()}\n",
        encoding="utf-8",
    )
    source = tmp_path / "sample_fact_consumer.py"
    source.write_text(
        "from wgsextract_cli.core.sample_workspace import SampleWorkspace, record\n"
        "w = SampleWorkspace('.')\n"
        "record('sex', 's.bam', 'Male', method='reads', confidence='high')\n"
        "w.record('platform', 's.bam', 'ont', method='reads', confidence='high')\n"
        "record('superpopulation', 's.bam', 'EUR', method='panel', confidence='high')\n"
        "w.record('target_depths', 's.bam', {'chrX': 15.0}, method='depth', confidence='high')\n"
        "record('sex', 's.bam', 'male', method='reads', confidence='high')\n"
        "w.record('sex', 's.bam', 'illumina', method='reads', confidence='high')\n"
        "record('superpopulation', 's.bam', 'Europe', method='panel', confidence='high')\n"
        "w.record('target_depths', 's.bam', {'chrX': '15'}, method='depth', confidence='high')\n"
        "record('new_fact', 's.bam', 1.0, method='reads', confidence='high')\n"
        "w.set_overrides({'sex': 'm', 'platform': 'nanopore'})\n"
        "w.set_overrides({'read_length': '15'})\n"
        "w.set_overrides({'sex': 1})\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(config),
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            "--follow-imports=silent",
            "--no-error-summary",
            "--no-pretty",
            str(source),
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    assert result.returncode == 1, result.stdout + result.stderr
    assert {int(line.split(": error:", 1)[0].rsplit(":", 1)[1]) for line in errors} == {
        7,
        8,
        9,
        10,
        11,
        13,
        14,
    }, result.stdout
