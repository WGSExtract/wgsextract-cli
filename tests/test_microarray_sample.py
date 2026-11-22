import logging

import pytest

from tests.htslib_helpers import needs_htslib
from tests.test_microarray_simulation import LENGTH, _library, _microarray, _rows
from tests.test_microarray_simulation import (
    _synthetic_templates_only as _synthetic_templates_only,
)
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib


@pytest.mark.parametrize(
    "layout", ["plain", "bgzf", "unsorted", "unsorted-bgzf", "partial"]
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
    kits = []
    for kind, declared in [
        ("complete", ("1", "X")),
        ("incomplete", ("X",) if layout == "partial" else ()),
    ]:
        vcf = tmp_path / f"{kind}.vcf"
        vcf.write_text(
            "##fileformat=VCFv4.2\n"
            + "".join(f"##contig=<ID={c},length={LENGTH}>\n" for c in declared)
            + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            + '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
            + '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Quality">\n'
            + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tOTHER\tPICK\n"
            + "".join(r.replace(" ", "\t") + "\n" for r in records)
        )
        if "bgzf" in layout:
            compressed = vcf.with_suffix(".vcf.gz")
            with compressed.open("wb") as stream:
                run_command(["bgzip", "-c", vcf], stdout=stream)
            vcf = compressed
        outdir = tmp_path / kind
        caplog.clear()
        with caplog.at_level(logging.INFO):
            _microarray(str(vcf), fasta, outdir, "--sample", "PICK")
        # Sorted BGZF can get the contig names from its index instead of scanning.
        assert ("missing VCF contig declarations" in caplog.text) == (
            kind == "incomplete" and layout != "bgzf"
        )
        kits.append(_rows(outdir / f"{kind}_PICK_CombinedKit.txt"))
    assert kits[0] == kits[1]
    assert kits[1]["rsHet"][-1] == "AC"
    assert kits[1]["rsAlt"][-1] == "CC"
    assert kits[1]["rsX"][-1] == "AG"
