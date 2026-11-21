"""Issue #1169: missing variants do not establish Y reference callability."""

import csv
import gzip
import json
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, write_fasta
from tests.test_lineage_haplogroups import _vcf
from tests.yleaf_helpers import native_tables as native_tables
from wgsextract_cli.core.reference_evidence import (
    DEPTH_ONLY,
    Provenance,
    ReferenceEvidence,
    State,
)
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.core.variant_confidence import Thresholds
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
    with (output / "variants/variants.out").open() as stream:
        inferred = [
            r for r in csv.DictReader(stream, delimiter="\t") if int(r["pos"]) >= 200
        ]
    assert {r["reads"] for r in inferred} == {"0"}
    assert {r["state"] for r in inferred} == {"D"}


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
    result = CommandResult(command="lineage y-haplogroup")
    with collecting(result):
        analyze(
            Path(vcf),
            output,
            build="hg38",
            reference=reference,
            assume_reference=assume_reference,
        )
    assert any(w.code == "y_reference_evidence_missing" for w in result.warnings)
    assessed = {str(pos) for pos in (110, 120, 130, 140) if pos <= end}
    with (output / "kit/kit.out").open() as stream:
        assert not {r["pos"] for r in csv.DictReader(stream, delimiter="\t")} & assessed
    with (output / "kit/kit.fmf").open() as stream:
        failed = list(csv.DictReader(stream, delimiter="\t"))
    assert {r["pos"] for r in failed} >= assessed
    assert next(r for r in failed if r["pos"] == "110")["reads"] == sample.split(":")[1]


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


def _marker_rows(output, sample, suffix):
    with (output / sample / f"{sample}.{suffix}").open() as stream:
        return {int(row["pos"]): row for row in csv.DictReader(stream, delimiter="\t")}


@pytest.mark.parametrize("indexed", [True, False])
def test_1169_y_vcf_preserves_both_aliases_with_or_without_index(
    tmp_path, native_tables, indexed
):
    vcf = Path(
        _vcf(
            tmp_path / "kit.vcf.gz",
            {"chr1": 1000, "chrY": 1000, "Y": 1000},
            "chr1\t100\t.\tA\tT\t50\tPASS\t.\tGT:DP\t1:20\n"
            "chrY\t130\t.\tA\tG\t50\tPASS\t.\tGT:DP\t1:20\n"
            "Y\t140\t.\tA\tT\t50\tPASS\t.\tGT:DP\t1:20\n",
            '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n',
        )
    )
    if not indexed:
        plain = vcf.with_suffix("")
        plain.write_bytes(gzip.decompress(vcf.read_bytes()))
        vcf = plain
    output = tmp_path / "result"
    analyze(vcf, output, build="hg38", no_ref_inference=True)
    assert {
        pos: row["called_base"]
        for pos, row in _marker_rows(output, "kit", "out").items()
    } == {130: "G"}
    assert _marker_rows(output, "kit", "fmf")[140]["called_base"] == "T"


@pytest.mark.parametrize("start", [100, 110])
@pytest.mark.parametrize("assume", [False, True])
def test_1169_overlapping_and_same_start_blocks_do_not_promote_no_calls(
    tmp_path, native_tables, start, assume
):
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        {"chrY": 1000},
        "chrY\t100\t.\tG\t<NON_REF>\t.\tPASS\tEND=160\tGT:MIN_DP:GQ\t0:20:60\n"
        f"chrY\t{start}\t.\tG\t<NON_REF>\t.\tPASS\tEND=140\tGT:DP:MIN_DP:GQ\t.:40:0:60\n"
        "chrY\t130\t.\tG\tA\t60\tPASS\t.\tGT:DP:GQ\t1:20:60\n",
        META,
    )
    reference = write_fasta(tmp_path / "reference.fa", {"chrY": "G" * 1000})
    output = tmp_path / "result"
    analyze(
        Path(vcf), output, build="hg38", reference=reference, assume_reference=assume
    )
    assert _marker_rows(output, "kit", "fmf")[120]["state"] == "NA"
    observed = _marker_rows(output, "kit", "out")[130]
    assert observed["called_base"] == "A" and observed["reads"] == "20"


@pytest.mark.parametrize("assume", [False, True])
@pytest.mark.parametrize("region", [None, "chrY:130-140"])
@pytest.mark.parametrize(
    "flt,gt,end",
    [
        ("PASS", "1", "."),
        ("LowQual", "1", "."),
        ("PASS", ".", "."),
        ("PASS", "1", "END=105"),
    ],
)
def test_1169_observed_multibase_record_interiors_are_never_assumed(
    tmp_path, native_tables, assume, flt, gt, end, region
):
    panel = tmp_path / "positions.txt"
    panel.write_text(
        "Y\tR\tR\t100\tA>G\tA\tG\n"
        "Y\tR1\tR1\t101\tA>C\tA\tC\n"
        "Y\tR1b\tR1b\t102\tA>G\tA\tG\n"
        "Y\tR1-extra\tR1\t104\tA>G\tA\tG\n"
        "Y\tleaf\tR1b1\t130\tA>G\tA\tG\n"
    )
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        {"chrY": 1000},
        f"chrY\t100\t.\tACG\tA\t60\t{flt}\t{end}\tGT:DP:GQ\t{gt}:20:60\n"
        "chrY\t130\t.\tA\tG\t60\tPASS\t.\tGT:DP:GQ\t1:20:60\n",
        META
        + '##FILTER=<ID=LowQual,Description="Failed">\n'
        + (
            Provenance("bcftools", "1.23", DEPTH_ONLY, region=region).header() + "\n"
            if region
            else ""
        ),
    )
    reference = write_fasta(
        tmp_path / "reference.fa", {"chrY": "A" * 99 + "ACG" + "A" * 898}
    )
    output = tmp_path / "result"
    analyze(
        Path(vcf),
        output,
        build="hg38",
        pos_file=panel,
        reference=reference,
        assume_reference=assume,
    )
    failed = _marker_rows(output, "kit", "fmf")
    assert failed[101]["state"] == failed[102]["state"] == "NA"
    if end != ".":
        assert failed[104]["state"] == "NA"


@pytest.mark.parametrize("assume", [False, True])
def test_1169_clair3_shadow_matches_shared_reference_evidence(
    tmp_path, native_tables, assume
):
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        {"chrY": 1000},
        "chrY\t99\t.\tG\tA\t60\tPASS\t.\tGT:DP:GQ\t.:40:60\n"
        "chrY\t100\t.\tG\t<NON_REF>\t.\tPASS\tEND=140\tGT:MIN_DP:GQ\t0/0:20:60\n",
        META + "##source=Clair3\n",
    )
    reference = write_fasta(tmp_path / "reference.fa", {"chrY": "G" * 1000})
    output = tmp_path / "result"
    analyze(
        Path(vcf), output, build="hg38", reference=reference, assume_reference=assume
    )
    assert _marker_rows(output, "kit", "fmf")[100]["state"] == "NA"
    assert _marker_rows(output, "kit", "out")[110]["called_base"] == "G"
    states = [
        s.state
        for s in ReferenceEvidence(vcf).spans(
            Thresholds(10, 10, 20, 20),
            [("chrY", 100, 100), ("chrY", 110, 110)],
        )
    ]
    assert states == [State.LOW_QUALITY_REFERENCE, State.CALLABLE_REFERENCE]


@pytest.mark.parametrize(
    "command,callable_ref",
    [
        ("call -m -g 10", True),
        ("call -m -g10", True),
        ("call -m --gvcf 10,20", True),
        ("call -m --gvcf=10", True),
        ("call -m sample-g5.vcf.gz", False),
        ("call -m -- '-g5'", False),
    ],
)
def test_1169_gvcf_option_detection_is_shared_and_not_inferred_from_filenames(
    tmp_path, native_tables, command, callable_ref
):
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        {"chrY": 1000},
        "chrY\t100\t.\tG\t<NON_REF>\t.\tPASS\tEND=120\tGT:MIN_DP:GQ\t0/0:20:.\n"
        "chrY\t130\t.\tA\tG\t60\tPASS\t.\tGT:DP:GQ\t1:20:60\n",
        META + f"##bcftools_callCommand={command}\n",
    )
    reference = write_fasta(tmp_path / "reference.fa", {"chrY": "G" * 1000})
    output = tmp_path / "result"
    analyze(Path(vcf), output, build="hg38", reference=reference)
    assert (120 in _marker_rows(output, "kit", "out")) is callable_ref
    state = next(
        ReferenceEvidence(vcf).spans(
            Thresholds(10, 10, 20, 20),
            [("chrY", 120, 120)],
        )
    ).state
    assert (state is State.CALLABLE_REFERENCE) is callable_ref


def test_1169_all_sites_reference_call_keeps_observed_ad_without_dp(
    tmp_path, native_tables
):
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        {"chrY": 1000},
        "chrY\t100\t.\tG\tA\t60\tPASS\t.\tGT:AD\t0:20,0\n",
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Depths">\n',
    )
    output = tmp_path / "result"
    result = CommandResult(command="lineage y-haplogroup")
    with collecting(result):
        analyze(Path(vcf), output, build="hg38", no_ref_inference=True)
    assert _marker_rows(output, "kit", "out")[100]["reads"] == "20"
    assert not any(w.code == "y_reference_evidence_missing" for w in result.warnings)


@pytest.mark.parametrize("gq", ["nan", "inf"])
def test_1169_nonfinite_reference_quality_is_rejected_by_both_readers(
    tmp_path, native_tables, gq
):
    vcf = _vcf(
        tmp_path / "kit.vcf.gz",
        {"chrY": 1000},
        f"chrY\t100\t.\tG\t<NON_REF>\t.\tPASS\tEND=120\tGT:MIN_DP:GQ\t0/0:20:{gq}\n",
        META.replace("ID=GQ,Number=1,Type=Integer", "ID=GQ,Number=1,Type=Float"),
    )
    reference = write_fasta(tmp_path / "reference.fa", {"chrY": "G" * 1000})
    output = tmp_path / "result"
    analyze(Path(vcf), output, build="hg38", reference=reference)
    assert _marker_rows(output, "kit", "fmf")[110]["state"] == "NA"
    assert (
        next(
            ReferenceEvidence(vcf).spans(
                Thresholds(10, 10, 20, 20),
                [("chrY", 110, 110)],
            )
        ).state
        is State.LOW_QUALITY_REFERENCE
    )
