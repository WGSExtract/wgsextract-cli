"""Regenerate checked-in output schemas; --check reports stale contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wgsextract_cli.core.output_schema import Shape, output_schema  # noqa: E402
from wgsextract_cli.core.panel_spec import panel_schema  # noqa: E402
from wgsextract_cli.core.release_recheck import recheck_schema  # noqa: E402
from wgsextract_cli.core.result_schema import result_schema  # noqa: E402
from wgsextract_cli.core.run_delta import delta_schema  # noqa: E402
from wgsextract_cli.core.sample_document import (  # noqa: E402
    SAMPLE_SHAPES,
    SCHEMA_DESCRIPTION,
    SampleDocument,
)
from wgsextract_cli.core.sample_types import SCHEMA_ID  # noqa: E402
from wgsextract_cli.core.step_documents import (  # noqa: E402
    SCHEMA as STEP_SCHEMA,
)
from wgsextract_cli.core.step_documents import (  # noqa: E402
    CompleteStepDocument,
    Digest,
    DirectoryOutput,
    FileIdentity,
    FileOutput,
    InputIdentity,
    JournalDocument,
    RunningStepDocument,
)

OUTPUT = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.sample.1.schema.json"
STEP_OUTPUT = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.steps.1.schema.json"
RESULT_OUTPUT = (
    ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.result.1.schema.json"
)
SCHEMAS = ROOT / "src/wgsextract_cli/assets/schemas"
# An input format, not an output: the research panel files `panel check` reads.
PANEL_INPUT = SCHEMAS / "wgsextract.panel.1.schema.json"


def documents() -> dict[Path, dict[str, object]]:
    sample = output_schema(
        SampleDocument,
        shapes=SAMPLE_SHAPES,
        schema_id=f"urn:wgsextract:schema:sample:{SCHEMA_ID.rsplit('/', 1)[1]}",
        title=SCHEMA_ID,
        description=SCHEMA_DESCRIPTION,
    )
    steps = output_schema(
        JournalDocument,
        shapes={
            JournalDocument: Shape("journal"),
            RunningStepDocument: Shape("runningStep"),
            CompleteStepDocument: Shape("completeStep"),
            InputIdentity: Shape("input"),
            FileOutput: Shape("fileOutput"),
            DirectoryOutput: Shape("directoryOutput"),
            Digest: Shape("digest"),
            FileIdentity: Shape("fileIdentity"),
        },
        schema_id="urn:wgsextract:schema:steps:1",
        title=STEP_SCHEMA,
        description="Internal step journal. New writer records are described by "
        "runningStep/completeStep definitions; unclassified historical records "
        "remain opaque objects and are not validated by the reader.",
    )
    return {
        OUTPUT: sample,
        STEP_OUTPUT: steps,
        RESULT_OUTPUT: result_schema(),
        SCHEMAS / "wgsextract.recheck.1.schema.json": recheck_schema(),
        SCHEMAS / "wgsextract.delta.1.schema.json": delta_schema(),
        PANEL_INPUT: panel_schema(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, document in documents().items():
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        parser.exit(
            1,
            "Output schemas are stale; run scripts/regen.py:\n"
            + "\n".join(stale)
            + "\n",
        )


if __name__ == "__main__":
    main()
