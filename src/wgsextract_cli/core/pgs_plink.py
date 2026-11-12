"""Optional independent sum check of the native match set with PLINK 2."""

from __future__ import annotations

import csv
import math
import subprocess
from pathlib import Path

from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.core.utils import WGSExtractError, popen


def crosscheck(matched: Path, work: Path, expected: float) -> dict[str, float]:
    """Recode matched diploid calls for PLINK, without changing allele counts.

    The check covers scoring arithmetic, not an independent harmonization or
    missingness policy. PLINK's text sums are rounded to six significant digits.
    """
    vcf = work / "matched.vcf"
    weights = work / "weights.tsv"
    sorter = [
        "bcftools",
        "sort",
        "-Ov",
        "-o",
        str(vcf),
        "-T",
        str(work / "sort"),
    ]
    with matched.open() as source, weights.open("w") as table:
        with popen(sorter, stdin=subprocess.PIPE, text=True) as process:
            assert process.stdin is not None
            stream = process.stdin
            stream.write(
                '##fileformat=VCFv4.2\n##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            )
            for chrom in range(1, 23):
                stream.write(f"##contig=<ID={chrom}>\n")
            stream.write(
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
            )
            table.write("ID\tA1\tWEIGHT\n")
            for number, row in enumerate(csv.DictReader(source, delimiter="\t")):
                if number % 10000 == 0:
                    checkpoint()
                gt = {"0": "1/1", "1": "0/1", "2": "0/0"}[row["dosage"]]
                stream.write(
                    f"{row['chrom']}\t{row['pos']}\t{row['id']}\t{row['effect']}\t{row['other']}\t.\tPASS\t.\tGT\t{gt}\n"
                )
                table.write(f"{row['id']}\t{row['effect']}\t{row['weight']}\n")
            stream.close()
        if process.returncode:
            raise WGSExtractError("bcftools sort failed during the PLINK cross-check.")
    prefix = work / "plink"
    with (work / "plink.stdout.log").open("w") as log:
        with popen(
            [
                "plink2",
                "--vcf",
                str(vcf),
                "--score",
                str(weights),
                "1",
                "2",
                "3",
                "header-read",
                "no-mean-imputation",
                "cols=+scoresums",
                "--out",
                str(prefix),
                "--threads",
                "1",
                "--memory",
                "1024",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        ) as plink_process:
            if plink_process.wait():
                raise WGSExtractError(
                    "PLINK 2 cross-check failed: "
                    + (work / "plink.stdout.log").read_text()[-800:]
                )
    with (work / "plink.sscore").open() as source:
        observed = float(next(csv.DictReader(source, delimiter="\t"))["WEIGHT_SUM"])
    difference = abs(observed - expected)
    if not math.isfinite(observed):
        raise WGSExtractError("PLINK reported a non-finite score.")
    rounding = (
        0.5 * 10 ** (math.floor(math.log10(abs(observed))) - 5) if observed else 0.0
    )
    if difference > 1e-6 + rounding:
        raise WGSExtractError(
            f"Native/PLINK sum disagreement: {expected} versus {observed}."
        )
    return {
        "score_sum": observed,
        "absolute_difference": difference,
        "text_rounding_bound": rounding,
        "absolute_tolerance": 1e-6,
    }
