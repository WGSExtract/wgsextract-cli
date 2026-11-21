"""Issue #1169: missing variants do not establish Y reference callability."""

import csv
import json
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, write_fasta
from tests.test_lineage_haplogroups import _vcf
from tests.yleaf_helpers import native_tables as native_tables
from wgsextract_cli.core.reference_evidence import DEPTH_ONLY, Provenance
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.ydna.yleaf_native.analysis import analyze
from wgsextract_cli.ydna.yleaf_native.custom_trees import file_tree

pytestmark = needs_htslib

META = (
    '##INFO=<ID=END,Number=1,Type=Integer,Description="Block end">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
    '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=FT,Number=1,Type=String,Description="Sample filter">\n'
)


@pytest.mark.parametrize("reference_alt", ["<NON_REF>", "<*>", "."])
@pytest.mark.parametrize("quality_model", ["gq", "bcftools", "provenance"])
def test_1169_variant_only_vcf_does_not_drift_to_reference_r1b(
    tmp_path, reference_alt, quality_model
):
    tree_path = tmp_path / "tree.json"
    tree_path.write_text(
        json.dumps(
            {
                'ROOT (Y-Chromosome "Adam")': ["AB"],
                "AB": ["E", "R1b"],
                "E": [],
                "R1b": ["R1b1"],
                "R1b1": [],
            }
        )
    )
    panel = tmp_path / "positions.txt"
    panel.write_text(
        "Y\tAB\tAB\t100\tA>G\tA\tG\n"
        "Y\tE\tE\t110\tA>G\tA\tG\n"
        + "".join(
            f"Y\tR{pos}\t{'R1b1' if pos == 202 else 'R1b'}\t{pos}\tA>G\tA\tG\n"
            for pos in range(200, 203)
        )
    )
    tree = file_tree(tree_path, panel, "hg38")
    sequence = list("A" * 1000)
    for pos in (110, 200, 201, 202):
        sequence[pos - 1] = "G"
    reference = write_fasta(tmp_path / "reference.fa", {"chrY": "".join(sequence)})
    variant = "chrY\t100\t.\tA\tG\t60\tPASS\t.\tGT:DP:GQ\t1:20:60\n"
    variants = _vcf(tmp_path / "variants.vcf.gz", {"chrY": 1000}, variant, META)
    gvcf = _vcf(
        tmp_path / "reference-blocks.vcf.gz",
        {"chrY": 1000},
        variant
        + f"chrY\t109\t.\tA\t{reference_alt}\t.\tPASS\tEND=115\tGT:MIN_DP:GQ\t0:20:{'60' if quality_model == 'gq' else '.'}\n",
        META
        + (
            "##bcftools_callCommand=call -m --ploidy 1 -g 10 -a GQ\n"
            if quality_model == "bcftools"
            else Provenance("bcftools", "1.23", DEPTH_ONLY).header() + "\n"
            if quality_model == "provenance"
            else ""
        ),
    )
    all_sites = _vcf(
        tmp_path / "all-sites.vcf.gz",
        {"chrY": 1000},
        variant + "chrY\t110\t.\tG\t.\t60\tPASS\t.\tGT:DP:GQ\t0:20:60\n",
        META,
    )

    def prediction(path, mode, assume=False):
        output = tmp_path / mode
        result = CommandResult(command="lineage y-haplogroup")
        with collecting(result):
            file = analyze(
                Path(path),
                output,
                build="hg38",
                tree=tree,
                reference=reference,
                assume_reference=assume,
            )
        with file.open() as stream:
            hg = next(csv.DictReader(stream, delimiter="\t"))["Hg"]
        return hg, result, output

    hg, result, output = prediction(variants, "variants")
    assert not hg.startswith("R1b") and hg != "E"
    warning = next(
        w for w in result.warnings if w.code == "y_reference_evidence_missing"
    )
    assert "gVCF" in warning.message and "all-sites VCF" in warning.message
    with (output / "variants" / "variants.fmf").open() as stream:
        failed = list(csv.DictReader(stream, delimiter="\t"))
    assert {r["pos"] for r in failed} == {"110", "200", "201", "202"}
    for path, mode in ((gvcf, "gvcf"), (all_sites, "all-sites")):
        hg, result, _ = prediction(path, mode)
        assert hg == "E"
        assert not any(
            w.code == "y_reference_evidence_missing" for w in result.warnings
        )
    hg, result, output = prediction(variants, "upstream", assume=True)
    assert hg == "R1b1"
    assert any(w.code == "y_reference_assumed" for w in result.warnings)
    assert json.loads((output / "run.json").read_text())["assume_reference"]


@pytest.mark.parametrize(
    "sample", ["0:20:.", "0:20:19", "0:9:60", "0:20:60:LowQual", ".:0:60"]
)
@pytest.mark.parametrize("assume_reference", [False, True])
@pytest.mark.parametrize("end", [110, 140])
def test_1169_reference_blocks_need_quality_depth_and_sample_filter(
    tmp_path, native_tables, sample, assume_reference, end
):
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        {"chrY": 1000},
        "chrY\t100\t.\tA\tG\t60\tPASS\t.\tGT:DP:GQ\t1:20:60\n"
        f"chrY\t110\t.\tA\t<NON_REF>\t.\tPASS\tEND={end}\tGT:MIN_DP:GQ:FT\t{sample}\n",
        META,
    )
    reference = write_fasta(tmp_path / "reference.fa", {"chrY": "A" * 1000})
    output = tmp_path / "results"
    analyze(
        Path(vcf),
        output,
        build="hg38",
        reference=reference,
        assume_reference=assume_reference,
    )
    assessed = {str(pos) for pos in (110, 120, 130, 140) if pos <= end}
    with (output / "kit/kit.out").open() as stream:
        assert not {r["pos"] for r in csv.DictReader(stream, delimiter="\t")} & assessed
    with (output / "kit/kit.fmf").open() as stream:
        failed = list(csv.DictReader(stream, delimiter="\t"))
    assert {r["pos"] for r in failed} >= assessed


def test_1169_upstream_opt_in_rejects_alignment_and_explicit_only_mode(tmp_path):
    for path, explicit in (("sample.bam", False), ("sample.vcf", True)):
        with pytest.raises(WGSExtractError, match="--assume-reference"):
            analyze(
                tmp_path / path,
                tmp_path / "results",
                build="hg38",
                assume_reference=True,
                no_ref_inference=explicit,
            )
