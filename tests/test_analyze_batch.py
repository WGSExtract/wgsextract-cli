import csv
import os
import shutil
import unittest
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, sam_to_bam, write_fasta
from tests.smoke_utils import REPO_ROOT, run_cli


@pytest.mark.parametrize(
    "contents",
    [
        "name\néchantillon\nsecond\n",
        "name,notes,input\néchantillon," + "x" * 1024 + ",\nsecond,,\n",
    ],
    ids=["single-column", "sample-ends-mid-row"],
)
def test_1247_batch_recovers_when_dialect_sniffing_fails(tmp_path, contents):
    batch = tmp_path / "batch.tsv"
    batch.write_text(contents, encoding="utf-8-sig")

    rc, _, stderr = run_cli(
        ["analyze", "comprehensive", "--batch", str(batch), "--outdir", str(tmp_path)]
    )

    assert rc == 1
    assert (tmp_path / "échantillon").is_dir()
    assert (tmp_path / "second").is_dir()
    assert "Batch analysis failed" in stderr


@pytest.mark.parametrize("contents", ["", "name,input,vcf\n"])
def test_1247_batch_does_not_report_success_without_any_samples(tmp_path, contents):
    batch = tmp_path / "empty.csv"
    batch.write_text(contents, encoding="utf-8")
    rc, _, stderr = run_cli(
        ["analyze", "comprehensive", "--batch", str(batch), "--outdir", str(tmp_path)]
    )
    assert rc == 2
    assert "--batch" in stderr and "sample row" in stderr


class TestAnalyzeBatch(unittest.TestCase):
    def setUp(self):
        self.outdir = str(REPO_ROOT / "out" / "tests" / "analyze_batch")
        if os.path.exists(self.outdir):
            shutil.rmtree(self.outdir)
        os.makedirs(self.outdir, exist_ok=True)

        self.scan_dir = os.path.join(self.outdir, "scan_dir")
        os.makedirs(self.scan_dir, exist_ok=True)

        # Create dummy sample files
        # Sample A: BAM + VCF
        os.makedirs(os.path.join(self.scan_dir, "sampleA"), exist_ok=True)
        self.sampleA_bam = os.path.join(self.scan_dir, "sampleA", "sampleA.bam")
        self.sampleA_vcf = os.path.join(self.scan_dir, "sampleA", "sampleA.vcf.gz")
        with open(self.sampleA_bam, "w") as f:
            f.write("dummy")
        with open(self.sampleA_vcf, "w") as f:
            f.write("dummy")

        # Sample B: CRAM
        os.makedirs(os.path.join(self.scan_dir, "sampleB"), exist_ok=True)
        self.sampleB_cram = os.path.join(self.scan_dir, "sampleB", "sampleB.cram")
        with open(self.sampleB_cram, "w") as f:
            f.write("dummy")

    def test_batch_gen_fails_for_a_directory_without_genomes(self):
        empty = os.path.join(self.outdir, "empty")
        os.makedirs(empty, exist_ok=True)
        batch_file = os.path.join(self.outdir, "empty-batch.csv")
        rc, stdout, stderr = run_cli(
            ["analyze", "batch-gen", "--directory", empty, "--output", batch_file]
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("No BAM, CRAM or VCF files found", stdout + stderr)
        self.assertFalse(os.path.exists(batch_file))

    def test_batch_gen(self):
        """Test generating a batch file from a directory."""
        batch_file = os.path.join(self.outdir, "batch.csv")
        args = [
            "analyze",
            "batch-gen",
            "--directory",
            self.scan_dir,
            "--output",
            batch_file,
        ]

        rc, stdout, stderr = run_cli(args)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(batch_file))

        with open(batch_file) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 2)

            # Check sampleA
            sampleA = next(r for r in rows if r["name"] == "sampleA")
            self.assertEqual(sampleA["input"], os.path.abspath(self.sampleA_bam))
            self.assertEqual(sampleA["vcf"], os.path.abspath(self.sampleA_vcf))

            # Check sampleB
            sampleB = next(r for r in rows if r["name"] == "sampleB")
            self.assertEqual(sampleB["input"], os.path.abspath(self.sampleB_cram))
            self.assertEqual(sampleB["vcf"], "")

    def test_batch_execution_mock(self) -> None:
        """Exercise real batch dispatch, with and without a supplied VCF."""
        sample = Path(self.outdir) / "inputs"
        sample.mkdir()
        # A short synthetic chrM is ineligible for rCRS lineage calling.
        ref = write_fasta(sample / "ref.fa", {"chr1": "ACGT" * 2500, "chrM": "A" * 500})
        bam = sam_to_bam(
            [
                "@HD\tVN:1.6\tSO:coordinate",
                "@SQ\tSN:chr1\tLN:10000",
                "@SQ\tSN:chrM\tLN:500",
                "@RG\tID:rg\tSM:S",
                "r1\t0\tchr1\t100\t60\t10M\t*\t0\t0\tTACGTACGTA\tIIIIIIIIII\tRG:Z:rg",
            ],
            sample / "sample.bam",
        )
        plain = sample / "sample.vcf"
        plain.write_text(
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=chr1,length=10000>\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "chr1\t101\t.\tA\tG\t50\tPASS\t.\n"
        )
        vcf = bgzip_index(plain)
        batch_file = os.path.join(self.outdir, "run_batch.csv")
        with open(batch_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["name", "input", "vcf"])
            writer.writerow(["Sample1", bam, vcf])
            writer.writerow(["Sample2", bam, ""])

        # Run with --skip-calling to avoid needing a full ref
        args = [
            "analyze",
            "comprehensive",
            "--batch",
            batch_file,
            "--outdir",
            self.outdir,
            "--ref",
            str(ref),
            "--skip-calling",
            "--debug",
        ]

        rc, stdout, stderr = run_cli(args)
        self.assertEqual(rc, 0, stderr)

        # Check logs for batch processing headers
        self.assertIn("### BATCH PROCESSING: Sample1", stdout)
        self.assertIn("### BATCH PROCESSING: Sample2", stdout)
        before, after = stdout.split("### BATCH PROCESSING: Sample2", 1)
        self.assertIn("Processing Input VCFs (1 files)", before)
        self.assertNotIn("Processing Input VCFs", after)

        # Check that output directories were created
        self.assertTrue(os.path.exists(os.path.join(self.outdir, "Sample1")))
        self.assertTrue(os.path.exists(os.path.join(self.outdir, "Sample2")))


if __name__ == "__main__":
    unittest.main()
