"""Generate report/1's typed common components without duplicating them.

The remaining section/finding contract is still handwritten in schema_sources;
it is kept separate from the generated artifact and migrated in later slices.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wgsextract_cli.core.output_schema import Shape, output_schema  # noqa: E402
from wgsextract_cli.core.report_documents import (  # noqa: E402
    ReportAttemptDocument,
    ReportLinkDocument,
    ReportMissingDocument,
    ReportSourceDocument,
    ReportWarningDocument,
)

SOURCE = ROOT / "scripts/schema_sources/wgsextract.report.1.json"
OUTPUT = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.report.1.schema.json"


def report_schema() -> dict[str, object]:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("$defs"), dict):
        raise TypeError("Report schema source must contain an object of definitions.")
    definitions: dict[str, object] = document["$defs"]
    shapes: dict[object, Shape] = {
        ReportAttemptDocument: Shape("reportAttempt", additional=False),
        ReportSourceDocument: Shape("reportSource", additional=False),
        ReportWarningDocument: Shape("reportWarning"),
        ReportMissingDocument: Shape("reportMissing", additional=False),
        ReportLinkDocument: Shape("reportLink", additional=False),
    }
    if {shape.name for shape in shapes.values()} & definitions.keys():
        raise TypeError("Typed report definitions must not also be handwritten.")
    properties = document["$defs"]["section"]["properties"]
    for field, name in (
        ("sources", "reportSource"),
        ("warnings", "reportWarning"),
        ("missing", "reportMissing"),
    ):
        if properties[field]["items"] != {"$ref": f"#/$defs/{name}"}:
            raise TypeError(f"Report {field} must reference its generated definition.")
    if document["$defs"]["finding"]["properties"]["links"]["items"] != {
        "$ref": "#/$defs/reportLink"
    }:
        raise TypeError("Report links must reference their generated definition.")
    for model in (
        ReportSourceDocument,
        ReportWarningDocument,
        ReportMissingDocument,
        ReportLinkDocument,
    ):
        name = shapes[model].name
        component = output_schema(
            model,
            shapes=shapes,
            schema_id=f"urn:wgsextract:report:component:{name}",
            title=name,
            description="A writer-used report component.",
        )
        nested = component.pop("$defs")
        for key in ("$schema", "$id", "title", "description"):
            component.pop(key)
        definitions[name] = component
        if isinstance(nested, dict):
            definitions.update(nested)
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(report_schema(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        actual = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if actual != text:
            sys.stderr.writelines(
                difflib.unified_diff(
                    actual.splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    fromfile=str(OUTPUT.relative_to(ROOT)),
                    tofile="generated report schema",
                )
            )
            parser.exit(1, "Report schema is stale; run scripts/regen.py.\n")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
