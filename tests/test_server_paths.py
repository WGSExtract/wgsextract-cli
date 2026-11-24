"""Focused server paths regressions."""

import json as json
import os as os
import typing as typing
from pathlib import Path as Path

import pytest as pytest

from tests.server_path_cases import _COMPANIONS as _COMPANIONS
from tests.server_path_cases import _DATA as _DATA
from tests.server_path_cases import _EVERY_PATH as _EVERY_PATH
from tests.server_path_cases import _base_request as _base_request
from tests.server_path_cases import _path_value as _path_value
from tests.test_server import _ALIGN_REQUESTS as _ALIGN_REQUESTS
from tests.test_server import _CALLER_OPS as _CALLER_OPS
from tests.test_server import _CALLER_PATHS as _CALLER_PATHS
from tests.test_server import _EXAMPLE_PATHS as _EXAMPLE_PATHS
from tests.test_server import _IN as _IN
from tests.test_server import _METHYLATION_PATHS as _METHYLATION_PATHS
from tests.test_server import _PET_ANALYZE_PATHS as _PET_ANALYZE_PATHS
from tests.test_server import _PET_ANALYZE_REQUESTS as _PET_ANALYZE_REQUESTS
from tests.test_server import _REF_PATHS as _REF_PATHS
from tests.test_server import _REGION_LISTS as _REGION_LISTS
from tests.test_server import _REPAIR_PATHS as _REPAIR_PATHS
from tests.test_server import _SIGN_PATHS as _SIGN_PATHS
from tests.test_server import _SIGN_REQUESTS as _SIGN_REQUESTS
from tests.test_server import _TYPING_PATHS as _TYPING_PATHS
from tests.test_server import _VCF_PATHS as _VCF_PATHS
from tests.test_server import _VEP_PATHS as _VEP_PATHS
from tests.test_server import _RecordingFlavour as _RecordingFlavour
from tests.test_server import _serve as _serve
from tests.test_server import client as client
from tests.test_server import fake_data as fake_data
from tests.test_server import slow_coverage as slow_coverage
from tests.test_server import variant_tables as variant_tables
from tests.test_server import work as work
from wgsextract_cli.api._params import json_schema as json_schema
from wgsextract_cli.core.config import settings as settings
from wgsextract_cli.core.utils import UsageError as UsageError
from wgsextract_cli.server.operations import PathGuard as PathGuard
from wgsextract_cli.server.operations import RequestError as RequestError
from wgsextract_cli.server.operations import find as find
from wgsextract_cli.server.operations import operations as operations
from wgsextract_cli.server.operations import params_from_json as params_from_json


@pytest.mark.parametrize(
    ("op", "params", "status", "code"),
    [
        ("info", {"input": "{outside}/a.bam"}, 403, "path_not_allowed"),
        ("info", {"input": "{work}/../outside/a.bam"}, 403, "path_not_allowed"),
        ("info", {"input": "{work}/escape/a.bam"}, 403, "path_not_allowed"),
        ("info", {"input": "a.bam"}, 400, "usage"),
        # #431: realign reads URLs on the command line; a server would fetch
        # whatever a request names.
        (
            "realign",
            {"input": "http://127.0.0.1:9/x.cram", "outdir": "{work}"},
            400,
            "usage",
        ),
        # Security review of PR #422: Windows opens UNC paths over SMB.
        ("info", {"input": "//attacker/share/a.bam"}, 403, "path_not_allowed"),
        ("info", {"input": "\\\\attacker\\share\\a.bam"}, 403, "path_not_allowed"),
        ("info", {"input": "{work}/a.bam", "bogus": 1}, 400, "usage"),
        ("vcf/snp", {"input": "{work}/a.bam", "threads": "four"}, 400, "usage"),
        # #1117: executable sidecars must remain local-only, even in allowed folders.
        (
            "vcf/gatk",
            {"input": "{work}/a.bam", "pair_hmm_neon": "{work}/neon.jar"},
            400,
            "usage",
        ),
        # #1131: native CUDA also loads executable code from its local JAR.
        (
            "vcf/gatk",
            {"input": "{work}/a.bam", "pair_hmm_cuda": "{work}/cuda.jar"},
            400,
            "usage",
        ),
        (
            "lineage/mt-haplogroup",
            {"input": "{work}/a.bam", "haplogrep_path": "/bin/sh"},
            400,
            "usage",
        ),
        # PR #426 review: a region may name a BED file the guard didn't see.
        (
            "extract/custom",
            {"input": "{work}/a.bam", "region": "{outside}/t.bed"},
            403,
            "path_not_allowed",
        ),
        (
            "extract/bam-subset",
            {"input": "{work}/a.bam", "fraction": 0.5, "region": "beds/t.bed"},
            400,
            "usage",
        ),
        # PR #442 review: with no input or outdir it saved into the server's
        # working folder.
        ("qc/fake-data", {}, 400, "usage"),
        # #346: comprehensive saves into the working folder without outdir.
        ("analyze/comprehensive", {"input": "{work}/a.bam"}, 400, "usage"),
        (
            "analyze/comprehensive",
            {"batch": "{work}/b.csv", "outdir": "{work}"},
            400,
            "usage",
        ),
        ("analyze/batch-gen", {"directory": "{work}"}, 400, "usage"),
        # PR #463 review: "" became None, which the path guard skips.
        ("analyze/batch-gen", {"directory": "{work}", "output": ""}, 400, "usage"),
        (
            "pet-align",
            {"r1": "{outside}/r1.fq", "species": "dog", "outdir": "{work}"},
            403,
            "path_not_allowed",
        ),
        # #351 security review: an unknown format name became part of the
        # template and output paths, so "../" read and wrote outside them.
        (
            "microarray",
            {"input": "{work}/a.bam", "outdir": "{work}", "formats": "../../x"},
            400,
            "usage",
        ),
        # PR #631 review: --svd-prefix names panel files the guard must see.
        (
            "qc/contamination",
            {"input": "{work}/a.bam", "svd_prefix": "{outside}/panel"},
            403,
            "path_not_allowed",
        ),
        (
            "qc/contamination",
            {"input": "{work}/a.bam", "svd_prefix": "{work}/escape/panel"},
            403,
            "path_not_allowed",
        ),
        ("nope", {}, 404, "unknown_op"),
        # PR #422 review: these were 500 "internal" errors, or jobs with
        # values the schema forbids.
        ("vcf/snp", {}, 400, "usage"),
        (
            "lineage/y-haplogroup",
            {"input": "{work}/a.bam", "min_reads": None},
            400,
            "usage",
        ),
    ],
)
def test_requests_are_validated_and_confined_to_allowed_folders(
    client, tmp_path, work, op, params, status, code
):
    outside = tmp_path / "outside"
    outside.mkdir()
    if hasattr(os, "symlink"):
        try:
            (work / "escape").symlink_to(outside, target_is_directory=True)
        except OSError:
            pass
    if "escape" in str(params) and not (work / "escape").is_symlink():
        pytest.skip("can't create symlinks here")
    body = {
        k: v.format(work=work, outside=outside) if isinstance(v, str) else v
        for k, v in params.items()
    }
    got, response = client.request("POST", f"/ops/{op}", body)
    assert (got, response["error"]["code"]) == (status, code)
    assert client.request("GET", "/jobs")[1]["jobs"] == []


@pytest.mark.parametrize(
    "path",
    [
        r"\\attacker\share\a.bam",
        "//attacker/share/a.bam",
        r"\/attacker/share/a.bam",
        r"\\?\UNC\attacker\share\a.bam",
        r"\\?\C:\data\a.bam",
        r"\\.\pipe\a.bam",
        r"\??\UNC\attacker\share\a.bam",
        r"C:\data\..\other\a.bam",
    ],
)
def test_path_guard_refuses_network_paths_without_opening_them(path):
    # Security review of PR #422: on Windows, checking `\\attacker\share\a.bam`
    # resolved it first, which connects to the host and sends the user's NTLM
    # credentials, before the 403.
    flavour = _RecordingFlavour()
    guard = PathGuard([r"C:\data"], pathmod=flavour)
    assert flavour.opened == [r"C:\data"]
    with pytest.raises(RequestError) as refused:
        guard.check_path("input", path)
    assert (refused.value.status, refused.value.code) == (403, "path_not_allowed")
    assert flavour.opened == [r"C:\data"]
    guard.check_path("input", "c:/DATA/sub/a.bam")
    assert flavour.opened[-1] == "c:/DATA/sub/a.bam"


@pytest.mark.parametrize(
    ("op", "body"),
    [
        ("bam/merge", {"inputs": [_IN, _IN], "output": "D:evil.bam"}),
        ("bam/unalign", {"input": _IN, "r1": "D:evil.fq", "r2": "b.fq"}),
        ("bam/unalign", {"input": _IN, "r1": "a.fq", "r2": "D:evil.fq"}),
        ("bam/unalign", {"input": _IN, "r1": "a.fq", "r2": "b.fq", "se": "D:e.fq"}),
    ],
    ids=["merge-output", "unalign-r1", "unalign-r2", "unalign-se"],
)
def test_path_guard_refuses_drive_relative_output_names(op, body):
    # Review of PR #442: on Windows `D:evil.bam` names a file in drive D's
    # working folder, outside every allowed root, yet passed as a bare name.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    request = params_from_json(find(op), {**body, "outdir": r"C:\data"})
    with pytest.raises(RequestError) as refused:
        guard.check(request)
    assert (refused.value.status, refused.value.code) == (400, "usage")
    assert "absolute path" in str(refused.value)
    # The same names without a drive are bare file names, saved in outdir.
    bare = {
        k: v.removeprefix("D:") if isinstance(v, str) else v for k, v in body.items()
    }
    guard.check(params_from_json(find(op), {**bare, "outdir": r"C:\data"}))


def test_server_offers_no_ref_command_that_fetches_a_url_or_saves_config():
    # #346: ref download fetches any URL and ref bootstrap saves config.toml.
    ref_ops = {name for name in operations() if name.startswith("ref/")}
    assert {"ref/index", "ref/status", "ref/clinvar"} <= ref_ops
    assert not ref_ops & {"ref/download", "ref/bootstrap"}


def test_server_does_not_offer_benchmark():
    # #478: benchmark downloads any --dataset-url, so it stays local only.
    assert "benchmark" not in operations()


@pytest.mark.parametrize("depth", [-1, float("nan")])
def test_a_negative_or_nan_depth_is_refused(depth):
    # Review of PR #454: --qc-min-depth refused them, the API didn't.
    with pytest.raises(UsageError, match="at least 0"):
        find("vcf/deepvariant").params(input=Path("a.bam"), qc_min_depth=depth)
    if depth == -1:
        with pytest.raises(RequestError):
            params_from_json(
                find("vcf/deepvariant"), {"input": "/a.bam", "qc_min_depth": depth}
            )
    schema = json_schema(find("vcf/deepvariant").params)
    assert schema["properties"]["qc_min_depth"]["minimum"] == 0
