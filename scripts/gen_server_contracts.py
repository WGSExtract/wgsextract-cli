"""Regenerate the checked-in HTTP response schemas and OpenAPI document."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from server_transport_typescript import typescript  # noqa: E402

from wgsextract_cli.server.http_api import openapi_document  # noqa: E402
from wgsextract_cli.server.transport_schema import response_schemas  # noqa: E402

SCHEMAS = ROOT / "src/wgsextract_cli/assets/schemas"


def contract_diff(path: Path, actual: str, expected: str) -> str:
    """Keep compact JSON artifacts readable in freshness failure logs."""
    before, after = actual, expected
    if path.suffix == ".json":
        try:
            before = json.dumps(json.loads(actual), indent=2, ensure_ascii=False) + "\n"
            after = (
                json.dumps(json.loads(expected), indent=2, ensure_ascii=False) + "\n"
            )
        except ValueError:
            pass
    difference = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"committed/{path.relative_to(ROOT).as_posix()}",
            tofile=f"generated/{path.relative_to(ROOT).as_posix()}",
        )
    )
    return difference or "JSON values and key order match; serialization differs.\n"


def artifacts() -> dict[Path, str]:
    documents = {
        SCHEMAS / "wgsextract.server.responses.1.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:wgsextract:server:responses:1",
            "$defs": response_schemas(ref_prefix="#/$defs/"),
        },
        SCHEMAS / "wgsextract.server.openapi.json": openapi_document(),
    }
    result = {}
    for path, document in documents.items():
        # All operation inputs make pretty OpenAPI exceed the repository's file cap.
        compact = path.name.endswith(".openapi.json")
        result[path] = (
            json.dumps(
                document,
                indent=None if compact else 2,
                separators=(",", ":") if compact else None,
                ensure_ascii=False,
            )
            + "\n"
        )
    result[ROOT / "gui/app/shared/generated/server-transport.ts"] = typescript()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, text in artifacts().items():
        if args.check:
            actual = path.read_text(encoding="utf-8") if path.exists() else ""
            if actual != text:
                stale.append(str(path.relative_to(ROOT)))
                sys.stderr.write(contract_diff(path, actual, text))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        parser.exit(
            1,
            "Stale server contracts; run scripts/regen.py:\n" + "\n".join(stale) + "\n",
        )


if __name__ == "__main__":
    main()
