"""The Python API records a command's inputs as the CLI does (#1367)."""

import dataclasses
import json
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools
from tests.smoke_utils import run_cli
from tests.test_trio_vcfs import _vcf
from wgsextract_cli import api


def _input_manifest(manifest: dict) -> tuple[set[tuple[str, str]], dict]:
    """The inputs' roles and names, and the input params given, of a manifest."""
    params = manifest["provenance"]["params"]
    return (
        {(i["role"], Path(i["path"]).name) for i in manifest["inputs"]},
        {k: params.get(k) for k in ("input", "vcf_input", "proband")},
    )


@needs_tools("bcftools", "bgzip", "tabix")
@pytest.mark.parametrize(
    ("argv", "make_params", "operation", "role"),
    [
        (
            ["vcf", "filter", "--vcf-input", "{child}", "--expr", "QUAL>30"],
            lambda child, _: api.VcfFilterParams(vcf_input=child, expr="QUAL>30"),
            api.vcf.filter,
            "vcf_input",
        ),
        (
            ["qc", "vcf", "--vcf-input", "{child}"],
            lambda child, _: api.QcVcfParams(vcf_input=child),
            api.qc.vcf,
            "vcf_input",
        ),
        (
            ["vcf", "trio", "--proband", "{child}", "--mother", "{mother}"]
            + ["--father", "{father}", "--mode", "recessive"],
            lambda child, parents: api.VcfTrioParams(
                proband=child, mother=parents[0], father=parents[1], mode="recessive"
            ),
            api.vcf.trio,
            "proband",
        ),
    ],
    ids=["vcf-filter", "qc-vcf", "vcf-trio"],
)
def test_1367_api_records_the_vcf_input_as_the_cli_does(
    tmp_path, argv, make_params, operation, role
):
    # Issue #1367: the API recorded --vcf-input's VCF as role "input" with
    # params.vcf_input null, unlike the CLI's "vcf_input".
    child = Path(_vcf(tmp_path / "child.vcf.gz", ["500 . T A 1/1:50"]))
    parents = [
        Path(_vcf(tmp_path / f"{name}.vcf.gz", ["500 . T A 0/1:50"], sample=name))
        for name in ("mother", "father")
    ]
    paths = {"child": child, "mother": parents[0], "father": parents[1]}
    rc, stdout, stderr = run_cli(
        [arg.format(**{k: str(v) for k, v in paths.items()}) for arg in argv]
        + ["--outdir", str(tmp_path / "cli"), "--json"]
    )
    assert rc == 0, stderr
    params = dataclasses.replace(make_params(child, parents), outdir=tmp_path / "api")
    result = operation(params)

    cli_inputs, cli_params = _input_manifest(json.loads(stdout))
    api_inputs, api_params = _input_manifest(result.to_dict())
    assert (role, "child.vcf.gz") in cli_inputs
    assert api_inputs == cli_inputs
    assert api_params == cli_params
    assert api_params[role] == str(child)
