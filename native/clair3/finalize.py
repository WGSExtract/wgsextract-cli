"""Preserve MPS ploidy and reference evidence on Clair3's pileup-only exits."""

import gzip
import subprocess
from argparse import Namespace
from pathlib import Path


def finalize_pileup(args: Namespace, output: Path) -> None:
    caller = Path(__file__).resolve().parents[1] / "clair3.py"
    temporary = output / "tmp"
    pileup = output / "pileup.vcf.gz"
    empty = temporary / "empty.vcf.gz"
    with gzip.open(pileup, "rt") as source, gzip.open(empty, "wt") as target:
        for line in source:
            if not line.startswith("#"):
                break
            target.write(line)

    def run(module: str, *options: str) -> None:
        subprocess.run([args.pypy, str(caller), module, *options], check=True)

    def sort(folder: Path, filename: Path, *options: str) -> None:
        run(
            "SortVcf",
            "--input_dir",
            str(folder),
            "--output_fn",
            str(filename),
            "--ref_fn",
            str(args.ref_fn),
            "--sampleName",
            str(args.sample_name),
            "--cmd_fn",
            str(temporary / "CMD"),
            "--contigs_fn",
            str(temporary / "CONTIGS"),
            *options,
        )

    nonvariant = temporary / "gvcf_tmp_output" / "non_var.gvcf"
    if args.gvcf:
        sort(
            nonvariant.parent,
            nonvariant,
            "--vcf_fn_suffix",
            ".tmp.gvcf",
            "--output_all_contigs_in_gvcf_header",
            str(args.output_all_contigs_in_gvcf_header),
        )
    merged = temporary / "merge_output"
    for contig in (temporary / "CONTIGS").read_text().splitlines():
        if not contig:
            continue
        run(
            "MergeVcf",
            "--pileup_vcf_fn",
            str(pileup),
            "--full_alignment_vcf_fn",
            str(empty),
            "--output_fn",
            str(merged / f"merge_{contig}.vcf"),
            "--platform",
            str(args.platform),
            "--ctgName",
            contig,
            "--ref_fn",
            str(args.ref_fn),
            "--samtools",
            str(args.samtools),
            "--sampleName",
            str(args.sample_name),
            "--qual",
            str(args.qual),
            "--print_ref_calls",
            str(args.print_ref_calls),
            "--haploid_precise",
            str(args.haploid_precise),
            "--haploid_sensitive",
            str(args.haploid_sensitive),
            "--gender",
            str(args.gender),
            "--par_regions_bed",
            str(args.par_regions_bed),
            "--gvcf",
            str(args.gvcf),
            "--non_var_gvcf_fn",
            str(nonvariant),
            "--gvcf_fn",
            str(merged / f"merge_{contig}.gvcf"),
        )
    sort(merged, output / "merge_output.vcf", "--vcf_fn_prefix", "merge")
    if args.gvcf:
        sort(
            merged,
            output / "merge_output.gvcf",
            "--vcf_fn_prefix",
            "merge",
            "--vcf_fn_suffix",
            ".gvcf",
            "--output_all_contigs_in_gvcf_header",
            str(args.output_all_contigs_in_gvcf_header),
        )
