"""The BAM page's coverage-state data source: has Detailed Coverage already run?"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "examples" / "WGSExtract" / "scripts" / "shared" / "coverage_state.py"


def coverage_done(*args: str) -> str:
    output = subprocess.run([sys.executable, str(SCRIPT), *args], check=True, capture_output=True, text=True).stdout
    return json.loads(output)["values"]["coverage_done"]


class CoverageStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.bam = self.directory / "sample.bam"
        self.bam.write_bytes(b"bam")
        os.utime(self.bam, (1_000, 1_000))

    def write_reports(self, folder: Path, mtime: int = 2_000, suffixes=("_coverage.tsv", "_coverage_bins.tsv")):
        folder.mkdir(parents=True, exist_ok=True)
        for suffix in suffixes:
            report = folder / f"sample.bam{suffix}"
            report.write_text("Seq Name\n")
            os.utime(report, (mtime, mtime))

    def test_done_only_when_both_reports_are_newer_than_the_input(self):
        self.assertEqual(coverage_done(str(self.bam)), "false")
        self.write_reports(self.directory, suffixes=("_coverage.tsv",))
        self.assertEqual(coverage_done(str(self.bam)), "false")
        self.write_reports(self.directory)
        self.assertEqual(coverage_done(str(self.bam)), "true")
        # The BAM changed after the reports were written: they no longer describe it.
        os.utime(self.bam, (3_000, 3_000))
        self.assertEqual(coverage_done(str(self.bam)), "false")

    def test_reports_are_looked_for_in_the_output_folder_when_one_is_set(self):
        output = self.directory / "output folder"
        self.write_reports(output)
        self.assertEqual(coverage_done(str(self.bam), str(output)), "true")
        self.assertEqual(coverage_done(str(self.bam), ""), "false")

    def test_no_input_is_not_done(self):
        self.assertEqual(coverage_done(), "false")
        self.assertEqual(coverage_done("", ""), "false")


if __name__ == "__main__":
    unittest.main()
