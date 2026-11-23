"""Generated response contracts must describe actual server responses (#929)."""

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated, TypedDict

import pytest
from jsonschema import Draft202012Validator

from scripts.json_lines import dump_lines
from scripts.server_transport_typescript import (
    report_typescript,
    result_typescript,
    typescript,
    typescript_records,
)
from tests.test_server import _serve
from wgsextract_cli.core.output_schema import Constraints
from wgsextract_cli.server.http_api import openapi_document
from wgsextract_cli.server.jobs import Job

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "src/wgsextract_cli/assets/schemas"


@pytest.mark.parametrize("nested", [False, True])
def test_929_duplicate_writer_names_cannot_silently_merge_typescript_records(
    nested: bool,
) -> None:
    class First(TypedDict):
        value: str

    class Second(TypedDict):
        other: int

    class Document(TypedDict):
        first: First
        second: Second

    with pytest.raises(TypeError, match="Duplicate"):
        if nested:
            typescript_records(
                {Document: "Document"}, names={First: "Repeated", Second: "Repeated"}
            )
        else:
            typescript_records({First: "Repeated", Second: "Repeated"})


def test_929_unsupported_writer_metadata_is_not_silently_discarded() -> None:
    class Document(TypedDict):
        value: Annotated[str, "unsupported-metadata"]

    with pytest.raises(TypeError, match="Unsupported"):
        typescript_records({Document: "Document"})


def test_929_empty_writer_enum_does_not_emit_invalid_typescript() -> None:
    class Document(TypedDict):
        value: Annotated[str, Constraints(enum=())]

    with pytest.raises(TypeError, match="at least one"):
        typescript_records({Document: "Document"})


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
    for name, generate in (
        ("server-transport", typescript),
        ("result-records", result_typescript),
        ("report-records", report_typescript),
    ):
        assert (ROOT / f"gui/app/shared/generated/{name}.ts").read_text(
            encoding="utf-8"
        ) == generate()
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


def test_1416_independent_openapi_operation_changes_merge_cleanly(
    tmp_path: Path,
) -> None:
    text = (SCHEMAS / "wgsextract.server.openapi.json").read_text(encoding="utf-8")
    base = json.loads(text)
    expand = (("paths",), ("components", "schemas"))
    # The layout gen_server_contracts.py writes, which the variants below share.
    assert dump_lines(base, expand=expand) == text
    first, _, third = list(base["paths"])[:3]
    ours, theirs = copy.deepcopy(base), copy.deepcopy(base)
    next(iter(ours["paths"][first].values()))["summary"] = "Ours."
    next(iter(theirs["paths"][third].values()))["summary"] = "Theirs."
    theirs["components"]["schemas"]["Theirs"] = {"type": "string"}
    for name, document in (("base", base), ("ours", ours), ("theirs", theirs)):
        (tmp_path / name).write_text(
            dump_lines(document, expand=expand), encoding="utf-8"
        )

    # One single-line document made any two such changes conflict.
    merged = subprocess.run(
        ["git", "merge-file", "-p", "ours", "base", "theirs"],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
    )

    assert merged.returncode == 0, merged.stdout
    expected = copy.deepcopy(theirs)
    expected["paths"][first] = ours["paths"][first]
    assert json.loads(merged.stdout) == expected


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


def test_1023_variant_pages_with_acmg_classes_carry_the_research_disclaimer(
    tmp_path: Path,
) -> None:
    """PR #1023 review: query_variants returned automated P/LP classes without
    the research-only caveat the table's metadata carries."""
    table = tmp_path / "acmg_annotated_variants.tsv"
    table.write_text(
        "chrom\tpos\tgene\tacmg_class\nchr1\t100\tMUTYH\tLP\nchr1\t200\tGENEX\t\n",
        encoding="utf-8",
    )
    with _serve(tmp_path, [tmp_path]) as client:
        status, body = client.request("GET", f"/variants?path={table}")
        assert status == 200
        validate(body, "VariantPage")
        assert body["disclaimer"].startswith("Research use only; not a diagnosis")
        for query in ("columns=chrom,pos", "gene=GENEX"):
            status, body = client.request("GET", f"/variants?path={table}&{query}")
            assert status == 200
            assert "disclaimer" not in body
