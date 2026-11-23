"""Y extractions of a female sample say they hold no Y-DNA."""

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, sam_to_bam
from wgsextract_cli import api

pytestmark = needs_htslib


@pytest.mark.parametrize(("sex", "warns"), [("Female", True), ("Male", False)])
def test_y_extraction_of_a_female_sample_warns(tmp_path, sex, warns):
    header = ["@SQ\tSN:chr1\tLN:1000", "@SQ\tSN:chrY\tLN:1000"]
    bam = sam_to_bam(header, tmp_path / "sample.bam")
    run_wgsextract("sample", "set", "--input", bam, "--sex", sex)
    result = api.extract.ydna_bam(api.YdnaBamParams(input=bam, outdir=tmp_path / "out"))
    assert ("sample_female" in {w.code for w in result.warnings}) == warns
