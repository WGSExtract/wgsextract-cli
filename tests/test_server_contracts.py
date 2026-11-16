"""Generated response contracts must describe actual server responses (#929)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.server_transport_typescript import typescript
from tests.test_server import _serve
from wgsextract_cli.server.http_api import openapi_document
from wgsextract_cli.server.jobs import Job

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "src/wgsextract_cli/assets/schemas"


def validate(value: object, name: str) -> None:
    schema = json.loads(
        (SCHEMAS / "wgsextract.server.responses.1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    schema["$ref"] = f"#/$defs/{name}"
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)


def test_929_checked_in_openapi_and_response_schemas_do_not_drift() -> None:
    assert (ROOT / "gui/app/shared/generated/server-transport.ts").read_text(
        encoding="utf-8"
    ) == typescript()
    subprocess.run(
        [sys.executable, "scripts/gen_server_contracts.py", "--check"],
        cwd=ROOT,
        check=True,
    )
    document = json.loads(
        (SCHEMAS / "wgsextract.server.openapi.json").read_text(encoding="utf-8")
    )
    assert document == openapi_document()
    components = document["components"]["schemas"]
    for schema in components.values():
        Draft202012Validator.check_schema(schema)

    def check_refs(value: object) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                prefix = "#/components/schemas/"
                assert value["$ref"].startswith(prefix)
                assert value["$ref"].removeprefix(prefix) in components
            for item in value.values():
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    check_refs(document)


@pytest.mark.parametrize("full", [False, True])
def test_929_job_projections_preserve_omission_and_extension_data(full: bool) -> None:
    job = Job("example", "info", "query", {"input": "sample.bam"})
    queued = job.view(full=full)
    validate(queued, "Job")
    assert "exit_code" not in queued
    assert "finished" not in queued
    assert ("params" in queued) is full
    job.status = "failed"
    job.exit_code = 0
    job.error = json.loads('{"kind": "failure", "message": "failed", "extension": [1]}')
    job.progress = {"stage": "reading", "fraction": 0.0}
    job.result = {
        "status": "error",
        "schema": "wgsextract.result/1",
        "provenance": {"extension": [1]},
        "details": {},
        "extension": {"custom": True},
    }
    value = job.view(full=full)
    validate(value, "Job")
    assert value["exit_code"] == 0
    assert value["error"] == job.error
    assert value["result"]["extension"] == {"custom": True}
    assert ("schema" in value["result"]) is full
    assert ("provenance" in value["result"]) is full
    assert ("details" in value["result"]) is full
    validate(job.summary(), "JobSummary")


@pytest.mark.parametrize("field", ["kind", "message"])
def test_989_job_error_known_fields_remain_strings(field: str) -> None:
    schema = json.loads(
        (SCHEMAS / "wgsextract.server.responses.1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    schema["$ref"] = "#/$defs/JobError"
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"kind": "failure", "message": "failed", "extra": [1]})
    assert validator.is_valid({})
    assert not validator.is_valid({field: 42})


def test_929_http_response_schemas_match_live_discovery_and_errors(
    tmp_path: Path,
) -> None:
    table = tmp_path / "example_variants.tsv"
    table.write_text("chrom\tpos\tgene\nchr1\t100\tBRCA1\n", encoding="utf-8")
    with _serve(tmp_path, [tmp_path]) as client:
        for route, model in (
            ("/health", "Health"),
            ("/", "Index"),
            ("/ops", "Operations"),
            ("/ops/info", "OperationDescription"),
            ("/jobs", "Jobs"),
        ):
            status, body = client.request("GET", route)
            assert status == 200
            validate(body, model)
        status, body = client.request("GET", "/jobs/missing")
        assert status == 404
        validate(body, "Error")
        status, body = client.request("GET", f"/variants?path={table}")
        assert status == 200
        validate(body, "VariantPage")
        assert body["rows"] == [{"chrom": "chr1", "pos": 100, "gene": "BRCA1"}]
