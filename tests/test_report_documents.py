"""Published source/warning components keep report/1's validation behavior."""

import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.gen_report_schema import report_schema

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.report.1.schema.json"


def source_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(
        {
            "$ref": "#/$defs/reportSource",
            "$defs": schema["$defs"],
        }
    )


def test_929_report_components_and_handwritten_rules_cannot_drift() -> None:
    published = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert published == report_schema()
    handwritten = json.loads(
        (ROOT / "scripts/schema_sources/wgsextract.report.1.json").read_text(
            encoding="utf-8"
        )
    )
    for key, value in handwritten.items():
        if key == "$defs":
            for name, definition in value.items():
                assert published[key][name] == definition
        else:
            assert published[key] == value
    subprocess.run(
        [sys.executable, "scripts/gen_report_schema.py", "--check"],
        cwd=ROOT,
        check=True,
    )


def test_929_source_records_preserve_nullable_and_legacy_latest_attempts() -> None:
    validate = source_validator()
    attempt = {
        "command": "",
        "status": "legacy-custom",
        "finished": None,
        "manifest": None,
    }
    assert validate.is_valid(attempt)
    assert validate.is_valid({**attempt, "latest_attempt": None})
    assert validate.is_valid({**attempt, "latest_attempt": attempt})
    bad_values: tuple[object, ...] = (True, 1, 1.5, [], {})
    for field in attempt:
        assert not validate.is_valid(
            {key: value for key, value in attempt.items() if key != field}
        )
        for value in bad_values:
            assert not validate.is_valid({**attempt, field: value})
            assert not validate.is_valid(
                {
                    **attempt,
                    "latest_attempt": {**attempt, field: value},
                }
            )
    for field in ("finished", "manifest"):
        assert validate.is_valid({**attempt, field: "legacy text"})
    for field in ("command", "status"):
        assert not validate.is_valid({**attempt, field: None})
    assert not validate.is_valid({**attempt, "unknown": 1})
    assert not validate.is_valid(
        {**attempt, "latest_attempt": {**attempt, "unknown": 1}}
    )
    assert not validate.is_valid({**attempt, "latest_attempt": {}})
    for value in ("text", True, 1, []):
        assert not validate.is_valid({**attempt, "latest_attempt": value})


def test_929_warning_extensions_remain_open_without_loosening_known_fields() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validate = Draft202012Validator(
        {
            "$ref": "#/$defs/reportWarning",
            "$defs": schema["$defs"],
        }
    )
    assert validate.is_valid({"code": "custom", "message": "", "details": [1, None]})
    for value in (
        {},
        {"code": "custom"},
        {"code": 1, "message": ""},
        {"code": "", "message": None},
    ):
        assert not validate.is_valid(value)


def test_929_missing_commands_and_links_remain_closed_string_records() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for name, valid in (
        ("reportMissing", {"what": "", "command": ""}),
        ("reportLink", {"label": "", "url": ""}),
    ):
        validate = Draft202012Validator(
            {
                "$ref": f"#/$defs/{name}",
                "$defs": schema["$defs"],
            }
        )
        assert validate.is_valid(valid)
        assert not validate.is_valid({})
        assert not validate.is_valid({**valid, "unknown": True})
        for field in valid:
            assert not validate.is_valid({**valid, field: None})
