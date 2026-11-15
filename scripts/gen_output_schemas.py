"""Regenerate checked-in output schemas; --check reports stale public contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wgsextract_cli.core.output_schema import output_schema  # noqa: E402
from wgsextract_cli.core.sample_document import (  # noqa: E402
    SAMPLE_SHAPES,
    SCHEMA_DESCRIPTION,
    SampleDocument,
)
from wgsextract_cli.core.sample_types import SCHEMA_ID  # noqa: E402

OUTPUT = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.sample.1.schema.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    document = output_schema(
        SampleDocument,
        shapes=SAMPLE_SHAPES,
        schema_id=f"urn:wgsextract:schema:sample:{SCHEMA_ID.rsplit('/', 1)[1]}",
        title=SCHEMA_ID,
        description=SCHEMA_DESCRIPTION,
    )
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != text:
            parser.exit(1, "Sample schema is stale; run scripts/regen.py.\n")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
