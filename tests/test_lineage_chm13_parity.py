"""Regression for #1167: CHM13 calls must reach Haplogrep in rCRS coordinates."""

import pytest

from tests.haplogrep_helpers import mini_tree as mini_tree
from tests.htslib_helpers import needs_htslib
from tests.test_lineage_haplogroups import _calls, _classify, _haplogrep, _vcf
from wgsextract_cli.core.ref_library.mitochondrial import (
    CHM13_CHRM_MD5,
    RCRS_MD5,
    pinned_sequences,
)

pytestmark = [needs_htslib, pytest.mark.usefixtures("mini_tree")]


@pytest.mark.parametrize("md5", ["", CHM13_CHRM_MD5])
def test_1167_chm13_vcf_reaches_haplogrep_only_after_rcrs_lifting(tmp_path, md5):
    """Equal-length CHM13 and GRCh38 inputs must classify the same coordinates."""
    rcrs, chm13 = pinned_sequences()
    chm13_vcf = _vcf(
        tmp_path / "chm13.vcf.gz",
        {},
        f"chrM\t174\t.\t{chm13[173]}\tG\t50\tPASS\t.\tGT\t1\n"
        "chrM\t9631\t.\tC\t.\t50\tPASS\t.\tGT\t0\n",
        "##contig=<ID=chrM,length=16569" + (f",md5={md5}" if md5 else "") + ">\n",
    )
    grch38_vcf = _vcf(
        tmp_path / "grch38.vcf.gz",
        {},
        f"chrM\t750\t.\t{rcrs[749]}\tG\t50\tPASS\t.\tGT\t1\n"
        "chrM\t10208\t.\tT\tC\t50\tPASS\t.\tGT\t1\n",
        f"##contig=<ID=chrM,length=16569,md5={RCRS_MD5}>\n",
    )
    tool = _haplogrep(tmp_path)
    lifted = _classify(chm13_vcf, tmp_path / "chm13", haplogrep_path=tool)
    baseline = _classify(grch38_vcf, tmp_path / "grch38", haplogrep_path=tool)
    chm13_call, grch38_call = _calls(tmp_path)
    assert chm13_call["records"] == grch38_call["records"]
    assert [row[1] for row in chm13_call["records"]] == ["750", "10208"]
    assert lifted.exit_code == baseline.exit_code == 0
    assert "mapped onto the rCRS" in lifted.details["mt_reference"]
