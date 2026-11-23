"""Tests for initial 100 bp viewer locus selection with real samtools."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GUI_ROOT = Path(__file__).resolve().parents[2]
CLI_ROOT = GUI_ROOT.parent
SCRIPTS_DIR = GUI_ROOT / "examples" / "WGSExtract" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import viewer_region


def find_samtools() -> str | None:
    for candidate in (
        CLI_ROOT / ".pixi/envs/default/bin/samtools",
        CLI_ROOT / ".pixi/envs/default/Library/bin/samtools.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("samtools")


def _parse_locus(locus: str) -> tuple[str, int, int]:
    chrom, coords = locus.split(":", 1)
    start_s, end_s = coords.split("-", 1)
    return chrom, int(start_s), int(end_s)


def _write_indexed_bam(
    samtools: str,
    directory: Path,
    fasta_path: Path,
    reads: list[tuple[str, str, int, int, str]],
) -> Path:
    """Build a coordinate-sorted, indexed BAM from (qname, rname, pos, mapq, seq) tuples."""
    fai = fasta_path.with_suffix(fasta_path.suffix + ".fai")
    subprocess.run([samtools, "faidx", str(fasta_path)], check=True)
    header_lines = ["@HD\tVN:1.6\tSO:coordinate"]
    for line in fai.read_text(encoding="utf-8").splitlines():
        name, length = line.split("\t")[:2]
        header_lines.append(f"@SQ\tSN:{name}\tLN:{length}")

    sam_lines = list(header_lines)
    for qname, rname, pos, mapq, seq in sorted(reads, key=lambda r: (r[1], r[2])):
        sam_lines.append(
            f"{qname}\t0\t{rname}\t{pos}\t{mapq}\t{len(seq)}M\t*\t0\t0\t{seq}\t{'I' * len(seq)}"
        )

    sam_path = directory / "reads.sam"
    bam_path = directory / "reads.bam"
    sam_path.write_text("\n".join(sam_lines) + "\n", encoding="utf-8")
    subprocess.run(
        [samtools, "view", "-bS", "-o", str(bam_path), str(sam_path)],
        check=True,
    )
    subprocess.run([samtools, "index", str(bam_path)], check=True)
    return bam_path


class ViewerRegionTests(unittest.TestCase):
    def test_safe_intervals_exclude_telomeres_and_cytoband_centromere(self) -> None:
        with tempfile.TemporaryDirectory(prefix="viewer-region-cyto-") as tmp:
            downloads = Path(tmp) / "downloads"
            downloads.mkdir()
            (downloads / "cytobands_hg38.tsv").write_text(
                "\n".join(
                    [
                        "chr1\t0\t121700000\tp36.33\tgneg",
                        "chr1\t121700000\t125100000\tp11.1\tacen",
                        "chr1\t125100000\t143200000\tq12\tgvar",
                        "chr1\t143200000\t248956422\tq44\tgpos100",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            intervals = viewer_region.safe_intervals("chr1", 248956422, tmp, "hg38")
            self.assertEqual(len(intervals), 2)
            # Telomeres excluded (>= 5 Mb from ends)
            self.assertGreater(intervals[0][0], 5_000_000)
            self.assertLessEqual(intervals[-1][1], 248956422 - 5_000_000)
            # Centromere + gvar + 2 Mb padding excluded
            self.assertLess(intervals[0][1], 121_700_000)
            self.assertGreater(intervals[1][0], 143_200_000)

    def test_picks_100bp_mapped_region_avoiding_telomere_ngap_and_low_mapq(self) -> None:
        samtools = find_samtools()
        if not samtools:
            self.skipTest("samtools is not installed")

        with tempfile.TemporaryDirectory(prefix="viewer-region-bam-") as tmp:
            root = Path(tmp)
            chrom_len = 200_000
            # Telomeres (first/last 20 kb) are N's; interior is repeating ACGT.
            unit = "ACGTACGTACGTACGTTGCATGCATGCATGCA"
            interior = (unit * ((chrom_len // len(unit)) + 2))[:chrom_len]
            seq = ("N" * 20_000) + interior[20_000 : chrom_len - 20_000] + ("N" * 20_000)
            fasta = root / "ref.fa"
            fasta.write_text(f">chr1\n{seq}\n>chr2\n{'ACGT' * 250}\n", encoding="utf-8")

            reads: list[tuple[str, str, int, int, str]] = []
            # Low-MAPQ reads at 25% probe center (50,000)
            low_start = 49_800
            for i in range(10):
                rseq = seq[low_start - 1 : low_start - 1 + 150]
                reads.append((f"low_{i}", "chr1", low_start, 0, rseq))

            # High-MAPQ reads with a heterozygous SNP at 35% probe center (70,000)
            good_start = 69_950
            ref_slice = seq[good_start - 1 : good_start - 1 + 150]
            mut_pos = 50  # 1-based genomic pos 70,000
            alt_base = "G" if ref_slice[mut_pos] != "G" else "T"
            mut_slice = ref_slice[:mut_pos] + alt_base + ref_slice[mut_pos + 1 :]
            for i in range(6):
                reads.append((f"good_{i}", "chr1", good_start, 60, mut_slice if i < 4 else ref_slice))

            bam = _write_indexed_bam(samtools, root, fasta, reads)
            locus = viewer_region.choose_initial_locus(
                str(bam),
                str(fasta),
                {"chr1": chrom_len, "chr2": 1000},
                samtools,
                is_alignment=True,
            )
            chrom, start, end = _parse_locus(locus)
            self.assertEqual(chrom, "chr1")
            self.assertEqual(end - start + 1, 100)
            self.assertGreaterEqual(start, good_start)
            self.assertLessEqual(end, good_start + 150)
            # Variant at 70,000 is inside the chosen 100 bp window
            self.assertLessEqual(start, 70_000)
            self.assertGreaterEqual(end, 70_000)

            # Same behavior on an indexed CRAM file
            cram = root / "reads.cram"
            subprocess.run(
                [samtools, "view", "-C", "-T", str(fasta), "-o", str(cram), str(bam)],
                check=True,
            )
            subprocess.run([samtools, "index", str(cram)], check=True)
            cram_locus = viewer_region.choose_initial_locus(
                str(cram),
                str(fasta),
                {"chr1": chrom_len, "chr2": 1000},
                samtools,
                is_alignment=True,
            )
            self.assertEqual(cram_locus, locus)

    def test_sparse_bam_falls_back_to_streaming_first_mapped_cluster(self) -> None:
        samtools = find_samtools()
        if not samtools:
            self.skipTest("samtools is not installed")

        with tempfile.TemporaryDirectory(prefix="viewer-region-sparse-") as tmp:
            root = Path(tmp)
            chrom_len = 200_000
            seq = ("ACGT" * (chrom_len // 4))[:chrom_len]
            fasta = root / "ref.fa"
            fasta.write_text(f">chr1\n{seq}\n", encoding="utf-8")

            # Place reads only at 83,200 (not near any fixed probe fraction).
            cluster = 83_200
            rseq = seq[cluster - 1 : cluster - 1 + 120]
            reads = [(f"r_{i}", "chr1", cluster, 60, rseq) for i in range(6)]
            bam = _write_indexed_bam(samtools, root, fasta, reads)

            locus = viewer_region.choose_initial_locus(
                str(bam),
                str(fasta),
                {"chr1": chrom_len},
                samtools,
                is_alignment=True,
            )
            chrom, start, end = _parse_locus(locus)
            self.assertEqual(chrom, "chr1")
            self.assertEqual(end - start + 1, 100)
            self.assertGreaterEqual(start, cluster - 200)
            self.assertLessEqual(end, cluster + 300)

    def test_tiny_contig_shorter_than_100bp(self) -> None:
        locus = viewer_region.choose_initial_locus(
            "unused.bam", "unused.fa", {"chr1": 40}, "samtools", is_alignment=True
        )
        self.assertEqual(locus, "chr1:1-40")

    def test_tool_argv_splits_pixi_wrapper_command(self) -> None:
        argv = viewer_region._tool_argv(
            "/Users/me/.pixi/bin/pixi run --no-install --frozen -e default samtools"
        )
        self.assertEqual(
            argv,
            [
                "/Users/me/.pixi/bin/pixi",
                "run",
                "--no-install",
                "--frozen",
                "-e",
                "default",
                "samtools",
            ],
        )


if __name__ == "__main__":
    unittest.main()
