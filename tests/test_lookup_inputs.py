"""Typed lookup inputs preserve HTTP/MCP's existing wire behavior (#929)."""

import json
from pathlib import Path
from urllib.parse import urlencode

from jsonschema import Draft202012Validator

from tests.server_helpers import Mcp, _serve


def test_929_optional_lookup_fields_are_omittable_not_nullable(tmp_path: Path) -> None:
    manifest = tmp_path / "example.manifest.json"
    manifest.write_text(
        json.dumps({"schema": "wgsextract.result/1", "provenance": {"test": True}}),
        encoding="utf-8",
    )
    mcp = Mcp("--allow", str(tmp_path))
    try:
        tools = {
            tool["name"]: tool
            for tool in mcp.request("tools/list", {})["result"]["tools"]
        }
        for extra in ({}, {"full": False}, {"full": True}):
            answer = mcp.call("read_manifest", path=str(manifest), **extra)
            assert answer["isError"] is False
            assert ("provenance" in answer["structuredContent"]) is bool(
                extra.get("full")
            )
        for name, bad_fields in (
            ("read_manifest", {"full": None}),
            ("read_manifest", {"full": 1}),
            ("query_variants", {"gene": None}),
            ("query_variants", {"limit": True}),
            ("query_variants", {"limit": "2"}),
            ("query_variants", {"limit": 0}),
            ("query_variants", {"limit": 1001}),
            ("query_variants", {"columns": None}),
            ("query_variants", {"columns": ["pos", 3]}),
            ("query_variants", {"columns": ["not_a_column"]}),
            ("query_variants", {"min_confidence": "unknown"}),
            ("report_section", {"section": None}),
            ("report_section", {"section": "unknown"}),
        ):
            arguments = {"path": str(manifest), **bad_fields}
            assert not Draft202012Validator(tools[name]["inputSchema"]).is_valid(
                arguments
            )
            answer = mcp.call(name, **arguments)
            assert answer["isError"] is True
            assert answer["structuredContent"]["error"]["code"] == "usage"
        for invalid_arguments in (
            {},
            {"path": ""},
            {"path": str(manifest), "unknown": True},
        ):
            answer = mcp.call("read_manifest", **invalid_arguments)
            assert answer["structuredContent"]["error"]["code"] == "usage"
    finally:
        assert mcp.close() == 0

    with _serve(tmp_path, [tmp_path]) as client:
        for full in ("yes", "false"):
            status, answer = client.request(
                "GET", f"/manifest?{urlencode({'path': str(manifest), 'full': full})}"
            )
            assert status == 200
            assert ("provenance" in answer) is (full == "yes")


def test_929_typed_variant_inputs_preserve_empty_filters_and_array_decoding(
    tmp_path: Path,
) -> None:
    table = tmp_path / "example_variants.tsv"
    table.write_text(
        "chrom\tpos\tgene\nchr1\t100\tGENEA\nchr1\t200\t\n", encoding="utf-8"
    )
    before = table.read_bytes()
    mcp = Mcp("--allow", str(tmp_path))
    try:
        answer = mcp.call(
            "query_variants", path=str(table), columns=["pos"], limit=1000
        )
        assert answer["structuredContent"]["rows"] == [{"pos": 100}, {"pos": 200}]
        answer = mcp.call("query_variants", path=str(table), gene="")
        assert answer["structuredContent"]["rows"] == []
        first = mcp.call("query_variants", path=str(table), columns=["pos"], limit=1)
        page = first["structuredContent"]
        second = mcp.call(
            "query_variants",
            path=str(table),
            columns=["pos"],
            limit=1,
            cursor=page["next_cursor"],
        )
        assert page["rows"] == [{"pos": 100}]
        assert second["structuredContent"]["rows"] == [{"pos": 200}]
        assert second["structuredContent"]["next_cursor"] is None
    finally:
        assert mcp.close() == 0
    with _serve(tmp_path, [tmp_path]) as client:
        status, answer = client.request(
            "GET",
            f"/variants?{urlencode({'path': str(table)})}&columns=pos,gene&columns=chrom&limit=1",
        )
        assert status == 200
        assert answer["rows"] == [{"pos": 100, "gene": "GENEA", "chrom": "chr1"}]
    assert table.read_bytes() == before
