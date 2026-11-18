"""Compatibility of parameter objects created before the VCF API split.

Protocol-0 fixtures were captured at a61d3e9a with portable Path reductions.
"""

import inspect
import pickle
from pathlib import Path

import pytest

from wgsextract_cli import api


@pytest.mark.parametrize(
    "cls,fixture,kwargs",
    [
        (
            api.VcfSnpParams,
            "snp",
            {"input": "legacy.bam", "region": "chr1:1-20", "ploidy": "2"},
        ),
        (
            api.VcfTableParams,
            "table",
            {"input": "legacy.vcf.gz", "outdir": "tables"},
        ),
        (
            api.VcfClinvarParams,
            "clinvar",
            {"input": "legacy.vcf.gz", "ref": "references", "threads": 2},
        ),
    ],
)
def test_826_pre_split_vcf_pickles_round_trip_and_keep_source_discovery(
    cls, fixture, kwargs
):
    fixtures = Path(__file__).parent / "fixtures" / "api_vcf_pre_split"
    legacy = pickle.loads(
        (fixtures / f"{fixture}.pickle").read_text(encoding="ascii").encode("ascii")
    )
    current = cls(**kwargs)

    assert legacy == current
    assert legacy.to_dict() == current.to_dict()
    assert pickle.loads(pickle.dumps(current)) == current
    assert f"class {cls.__name__}(" in inspect.getsource(cls)
