"""Install Lab must judge confidence-masked 5x exports, not permissive calls."""

from __future__ import annotations

import gzip
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.htslib_helpers import needs_tools
from wgsextract_cli.core.dependencies import get_tool_path

if TYPE_CHECKING:
    from scripts.installlab.oracle import Oracle

pytestmark = needs_tools("bcftools")
LAB_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "installlab"
STEM = "HG00096.hg19-mini"


@pytest.fixture
def export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Oracle, Path, Path]:
    monkeypatch.syspath_prepend(str(LAB_SCRIPTS))
    monkeypatch.delitem(sys.modules, "scripts.installlab.oracle", raising=False)
    from scripts.installlab.oracle import Oracle

    tool = get_tool_path("bcftools")
    assert tool is not None
    current = Oracle(tmp_path, "nonce", {"bcftools": tool})
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    output = tmp_path / "workload" / "microarray"
    output.mkdir(parents=True)
    header = (
        "##fileformat=VCFv4.2\n##contig=<ID=20,length=1600>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Quality">\n'
        '##FILTER=<ID=LowQual,Description="Filtered">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
    )
    truth, calls, kit, reasons = [], [], [], []
    for pos in range(1, 1601):
        alt, gt, dp, gq, filtered = "G", "0/1", "5", "40", "PASS"
        genotype, reason = "--", "low_dp"
        if pos == 1:
            dp, genotype, reason = "12", "AG", "called"
        elif pos == 2:
            alt, gt, dp, gq = ".", "0/0", "12", "."
            genotype, reason = "AA", "called"
        elif pos == 3:
            dp, gq, reason = "12", "1", "low_gq"
        elif pos == 4:
            dp, filtered, reason = "12", "LowQual", "filtered"
        elif pos == 5:
            dp, gq, reason = "12", ".", "missing_gq"
        elif pos == 6:
            dp, reason = ".", "missing_dp"
        truth.append(f"20\t{pos}\trs{pos}\tA\tG\t60\tPASS\t.\tGT\t{gt}\n")
        calls.append(
            f"20\t{pos}\trs{pos}\tA\t{alt}\t60\t{filtered}\t.\tGT:DP:GQ\t{gt}:{dp}:{gq}\n"
        )
        kit.append(f"rs{pos}\t20\t{pos}\t{genotype}\n")
        reasons.append(f"rs{pos}\t20\t{pos}\t{genotype}\t{reason}\n")
    with gzip.open(dataset / f"{STEM}.vcf.gz", "wt") as stream:
        stream.write(header + "".join(truth))
    with gzip.open(output / f"{STEM}_combined.vcf.gz", "wt") as stream:
        stream.write(header + "".join(calls))
    (output / f"{STEM}_CombinedKit.txt").write_text(
        "# RSID\tCHROM\tPOS\tRESULT\n" + "".join(kit)
    )
    (output / f"{STEM}_CombinedKit_reasons.tsv").write_text(
        "# RSID\tCHROM\tPOS\tRESULT\tCALL_REASON\n" + "".join(reasons)
    )
    (output / f"{STEM}_23andMe_V5.txt").write_text(
        "# rsid\tchromosome\tposition\tgenotype\n" + "rs0\t20\t0\t--\n" * 600_001
    )
    return current, dataset, output


def test_918_five_x_no_calls_are_not_genotype_disagreements(
    export: tuple[Oracle, Path, Path],
) -> None:
    current, dataset, _ = export
    current._microarray(dataset)
    check = current.checks[-1]
    assert check.ok
    assert "2/2 callable" in check.detail
    assert "1598/1600 no-calls" in check.detail


@pytest.mark.parametrize(
    "corruption",
    [
        "weak_call",
        "all_no_calls",
        "wrong_reason",
        "duplicate_weak",
        "wrong_weak_reason",
        "missing_weak_reason",
        "duplicate_reason",
        "duplicate_correct_reason",
    ],
)
def test_918_oracle_rejects_confidence_policy_drift(
    export: tuple[Oracle, Path, Path], corruption: str
) -> None:
    current, dataset, output = export
    kit = output / f"{STEM}_CombinedKit.txt"
    reasons = output / f"{STEM}_CombinedKit_reasons.tsv"
    if corruption == "weak_call":
        kit.write_text(kit.read_text().replace("rs7\t20\t7\t--", "rs7\t20\t7\tAG"))
        reasons.write_text(
            reasons.read_text().replace(
                "rs7\t20\t7\t--\tlow_dp", "rs7\t20\t7\tAG\tcalled"
            )
        )
    elif corruption == "all_no_calls":
        kit.write_text(
            kit.read_text().replace("\tAG\n", "\t--\n").replace("\tAA\n", "\t--\n")
        )
    elif corruption == "wrong_reason":
        reasons.write_text(
            reasons.read_text().replace(
                "rs1\t20\t1\tAG\tcalled", "rs1\t20\t1\tAG\tlow_dp"
            )
        )
    elif corruption == "duplicate_weak":
        kit.write_text(kit.read_text() + "rs7\t20\t7\t--\n")
    elif corruption == "duplicate_reason":
        reasons.write_text(
            reasons.read_text().replace(
                "rs7\t20\t7\t--\tlow_dp",
                "rs7\t20\t7\t--\tlow_gq\nrs7\t20\t7\t--\tlow_dp",
            )
        )
    elif corruption == "duplicate_correct_reason":
        reasons.write_text(reasons.read_text() + "rs7\t20\t7\t--\tlow_dp\n")
    else:
        replacement = "low_gq" if corruption == "wrong_weak_reason" else ""
        reasons.write_text(
            reasons.read_text().replace(
                "rs7\t20\t7\t--\tlow_dp", f"rs7\t20\t7\t--\t{replacement}"
            )
        )
    current._microarray(dataset)
    assert not current.checks[-1].ok


def test_918_callable_genotypes_still_require_independent_truth_agreement(
    export: tuple[Oracle, Path, Path],
) -> None:
    current, dataset, _ = export
    truth = dataset / f"{STEM}.vcf.gz"
    with gzip.open(truth, "rt") as stream:
        content = stream.read()
    with gzip.open(truth, "wt") as stream:
        stream.write(
            content.replace(
                "20\t1\trs1\tA\tG\t60\tPASS\t.\tGT\t0/1",
                "20\t1\trs1\tA\tG\t60\tPASS\t.\tGT\t1/1",
            )
        )
    current._microarray(dataset)
    assert not current.checks[-1].ok
