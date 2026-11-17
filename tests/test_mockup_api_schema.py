"""The v2 mockups' API schema (gui/mockups/shared/data/api-schema.js) is
generated from the server's OpenAPI document and the CLI snapshot. The tool
pages build their requests from it and the mock server checks requests
against it, so a stale copy lets the mockups drift from the real API."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "gui/mockups/build_api_schema.py"
OPENAPI = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.server.openapi.json"
SNAPSHOT = ROOT / "tests/fixtures/api_cli_snapshot.json"
SCHEMA = ROOT / "gui/mockups/shared/data/api-schema.js"


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_api_schema", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mockup_api_schema_matches_openapi_and_cli() -> None:
    assert OPENAPI.is_file() and SNAPSHOT.is_file()
    expected = load_generator().build()
    assert SCHEMA.read_text(encoding="utf-8") == expected, (
        "gui/mockups/shared/data/api-schema.js is stale; "
        "run python3 gui/mockups/build_api_schema.py"
    )
