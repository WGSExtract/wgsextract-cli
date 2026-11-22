import gzip
import json
import logging
import os

import pytest

from tests.htslib_helpers import needs_htslib
from tests.test_microarray_simulation import LENGTH, _library, _microarray, _rows
from tests.test_microarray_simulation import (
    _synthetic_templates_only as _synthetic_templates_only,
)
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib


@pytest.mark.parametrize(
    "layout",
    [
        "plain",
        "bgzf",
        "indexed-bgzf",
        "indexed-alias-bgzf",
        "gzip",
        "unsorted",
        "unsorted-bgzf",
        "partial",
        "off-target",
        "region",
        "gvcf",
    ],
)
def test_issue_1341_selects_sample_with_undeclared_contigs(tmp_path, caplog, layout):
    fasta, _ = _library(tmp_path / "reference")
    records = [
        "1 1000 . A C 50 PASS . GT:DP:GQ 0/0:30:60 0/1:30:60",
        "1 2000 . A C 50 PASS . GT:DP:GQ 0/1:30:60 1/1:30:60",
        "X 1000 . A G 50 PASS . GT:DP:GQ 0/0:30:60 0/1:30:60",
    ]
    if layout.startswith("unsorted"):
        records.reverse()
        records.insert(-1, "1 1000 . A G 50 PASS . GT:DP:GQ 0/0:30:60 1/1:30:60")
    if layout == "off-target":
        records.insert(0, "1 1000 . A G 50 PASS . GT:DP:GQ 0/0:30:60 1/1:30:60")
        records.append("1 999 . A G 50 PASS . GT:DP:GQ 0/0:30:60 1/1:30:60")
    extra_headers = ""
    if layout == "gvcf":
        extra_headers = (
            '##INFO=<ID=END,Number=1,Type=Integer,Description="End position">\n'
            '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">\n'
        )
        records.insert(
            2,
            "1 2500 . A <NON_REF> . PASS END=3500 GT:GQ:MIN_DP 0/0:60:2 0/0:60:30",
        )
    kits = []
    for kind, declared in [
        ("complete", ("1", "X")),
        ("incomplete", ("X", "chr1") if layout == "partial" else ()),
    ]:
        if layout == "indexed-alias-bgzf":
            declared += ("chr1", "chrX")
        vcf = tmp_path / f"{kind}.vcf"
        vcf.write_text(
            "##fileformat=VCFv4.2\n"
            + "".join(f"##contig=<ID={c},length={LENGTH}>\n" for c in declared)
            + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            + '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
            + '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Quality">\n'
            + extra_headers
            + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tOTHER\tPICK\n"
            + "".join(r.replace(" ", "\t") + "\n" for r in records)
        )
        if "bgzf" in layout:
            compressed = vcf.with_suffix(".vcf.gz")
            with compressed.open("wb") as stream:
                run_command(["bgzip", "-c", vcf], stdout=stream)
            vcf = compressed
            if layout.startswith("indexed"):
                run_command(["tabix", "-p", "vcf", vcf])
        elif layout == "gzip":
            compressed = vcf.with_suffix(".vcf.gz")
            compressed.write_bytes(gzip.compress(vcf.read_bytes()))
            vcf = compressed
        outdir = tmp_path / kind
        region = ("--region", "1") if layout == "region" else ()
        caplog.clear()
        with caplog.at_level(logging.INFO):
            _microarray(str(vcf), fasta, outdir, "--sample", "PICK", *region)
        # Sorted BGZF can get the contig names from its index instead of scanning.
        assert ("missing VCF contig declarations" in caplog.text) == (
            kind == "incomplete" and not layout.startswith("indexed")
        )
        kits.append(_rows(outdir / f"{kind}_PICK_CombinedKit.txt"))
    assert kits[0] == kits[1]
    assert kits[1]["rsHet"][-1] == "AC"
    assert kits[1]["rsAlt"][-1] == "CC"
    if layout == "region":
        assert "rsX" not in kits[1]
    else:
        assert kits[1]["rsX"][-1] == "AG"
    if layout == "gvcf":
        assert kits[1]["rsRef"][-1] == "AA"


def test_issue_1341_stale_no_call_sample_kit_is_regenerated(tmp_path):
    fasta, _ = _library(tmp_path / "reference")
    vcf = tmp_path / "cohort.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=1,length=5000>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Quality">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tOTHER\tPICK\n"
        "1\t1000\t.\tA\tC\t50\tPASS\t.\tGT:DP:GQ\t0/0:30:60\t0/1:30:60\n"
    )
    outdir = tmp_path / "out"

    def run():
        with collecting(CommandResult(command="microarray")):
            _microarray(str(vcf), fasta, outdir, "--sample", "PICK")

    run()
    kit = outdir / "cohort_PICK_CombinedKit.txt"
    assert _rows(kit)["rsHet"][-1] == "AC"
    stamp = kit.stat()
    kit.write_text(kit.read_text().replace("rsHet\t1\t1000\tAC", "rsHet\t1\t1000\t--"))
    # Match the saved output identity: the reuse policy is the only stale input.
    os.utime(kit, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert _rows(kit)["rsHet"][-1] == "--"
    assert (outdir / f"{kit.name}.inputs.json").is_file()
    for receipt in outdir.glob("*.inputs.json"):
        data = json.loads(receipt.read_text())
        data["inputs"].pop("vcf_sample_preparation_policy", None)
        receipt.write_text(json.dumps(data))
    run()
    assert _rows(kit)["rsHet"][-1] == "AC"
