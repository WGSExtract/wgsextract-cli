"""Regional CRAM work must not decode unrelated chromosomes (issue 854)."""

import csv
import logging
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, sam_to_bam, write_fasta
from tests.yleaf_helpers import native_tables as native_tables
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib


@pytest.fixture
def regional_cram(tmp_path):
    complete = write_fasta(
        tmp_path / "complete.fa", {"chr1": "C" * 1000, "chrY": "A" * 1000}
    )
    bam = sam_to_bam(
        [
            "@HD\tVN:1.6\tSO:coordinate",
            "@SQ\tSN:chr1\tLN:1000",
            "@SQ\tSN:chrY\tLN:1000",
            "other\t0\tchr1\t1\t60\t100M\t*\t0\t0\t" + "C" * 100 + "\t" + "I" * 100,
            *[
                f"y{i}\t0\tchrY\t80\t60\t100M\t*\t0\t0\t"
                + "".join(
                    "G" if p in (100, 110, 120, 130) else "A" for p in range(80, 180)
                )
                + "\t"
                + "I" * 100
                for i in range(20)
            ],
        ],
        tmp_path / "sample.bam",
    )
    cram = tmp_path / "sample.cram"
    run_command(["samtools", "view", "-C", "-T", str(complete), "-o", str(cram), bam])
    run_command(["samtools", "index", str(cram)])
    complete.unlink()
    Path(str(complete) + ".fai").unlink()
    reference = write_fasta(tmp_path / "hg38.fa", {"chrY": "A" * 1000})
    return cram, reference


@pytest.mark.parametrize("command", ["calculate-coverage", "coverage-sample"])
def test_854_regional_cram_coverage_never_scans_other_contigs(
    regional_cram, tmp_path, caplog, command
):
    cram, reference = regional_cram
    caplog.set_level(logging.DEBUG)
    result = CommandResult(command=f"info {command}")
    with collecting(result):
        run_wgsextract(
            "info",
            command,
            "--input",
            str(cram),
            "--ref",
            str(reference),
            "--region",
            "chrY:80-179",
            "--outdir",
            str(tmp_path / command),
            "--format",
            "tsv",
        )
    assert result.details["breadth"] == {
        "Y": "~100 %" if command == "coverage-sample" else "100 %"
    }
    assert "gender" not in result.details
    # contract: regional CRAM work may not invoke idxstats, even if a decoder
    # happens to find another chromosome's reference in a machine-wide cache.
    assert "idxstats" not in caplog.text
    assert not list((tmp_path / command).glob("*.wgse_info.json"))
    table = next((tmp_path / command).glob("*_breadth.tsv"))
    assert "100 %" in table.read_text()


def test_854_yhap_cram_uses_only_indexed_y_evidence(
    regional_cram, native_tables, tmp_path, caplog
):
    cram, reference = regional_cram
    caplog.set_level(logging.DEBUG)
    run_wgsextract(
        "lineage",
        "y-haplogroup",
        "--input",
        str(cram),
        "--ref",
        str(reference),
        "--build",
        "hg38",
        "--outdir",
        str(tmp_path / "yhap"),
    )
    output = tmp_path / "yhap/yleaf"
    with (output / "hg_prediction.hg").open() as stream:
        prediction = next(csv.DictReader(stream, delimiter="\t"))
    assert prediction["Hg"] == "R1b1"
    assert prediction["Total_reads"] == "not assessed (regional Y query)"
    # contract: neither the sex guard nor Y extraction may run whole-CRAM idxstats.
    assert "idxstats" not in caplog.text
