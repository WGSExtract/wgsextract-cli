"""Result writer models preserve the shipped result/1 contract."""

import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from wgsextract_cli.core.result_schema import result_schema
from wgsextract_cli.core.results import (
    CommandResult,
    ReferenceRecord,
    load_schema,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.result.1.schema.json"


def test_929_committed_result_schema_follows_actual_writer_models() -> None:
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == result_schema()
    subprocess.run(
        [sys.executable, "scripts/gen_output_schemas.py", "--check"],
        cwd=ROOT,
        check=True,
    )


def test_929_result_records_preserve_bounds_nulls_and_extension_policies(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("real input\n", encoding="utf-8")
    result = CommandResult(command="extract")
    result.add_input(source)
    result.add_output(source, "output")
    result.warn("example_warning", "A warning.", count=1)
    result.provenance.reference = ReferenceRecord(str(source))
    result.set_details({"future": {"nested": [1, True, None, "extension"]}})
    result.finalize(probe_tools=False)
    document = result.to_dict()
    schema = load_schema()
    validator = Draft202012Validator(schema)
    assert validator.is_valid(document)
    for field in document:
        assert not validator.is_valid(
            {key: value for key, value in document.items() if key != field}
        )
    for field, value in (
        ("schema", "wgsextract.result/2"),
        ("command", ""),
        ("status", "unknown"),
        ("exit_code", -1),
        ("exit_code", 256),
        ("exit_code", True),
        ("exit_code", 1.5),
        ("error", {"kind": "unknown", "message": ""}),
        ("warnings", [{"code": "Not_a_code", "message": "", "details": {}}]),
        ("metrics", {"count": []}),
        ("details", []),
        ("unknown", True),
    ):
        assert not validator.is_valid({**document, field: value})
    for details in (None, {}, {"unknown": [1, {"anything": False}]}):
        assert validator.is_valid({**document, "details": details})
    file_validator = Draft202012Validator(
        {"$ref": "#/$defs/file", "$defs": schema["$defs"]},
    )
    record = result.to_dict()["inputs"]
    assert isinstance(record, list)
    file_document = record[0]
    assert file_validator.is_valid(file_document)
    for field, value in (
        ("size_bytes", -1),
        ("fingerprint", "not-a-fingerprint"),
        ("mtime", 1),
        ("unknown", True),
    ):
        assert not file_validator.is_valid({**file_document, field: value})
    for field in ("size_bytes", "mtime", "fingerprint", "index"):
        assert file_validator.is_valid({**file_document, field: None})
