"""Regenerate checked-in output schemas; --check reports stale contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wgsextract_cli.core import roh_document  # noqa: E402
from wgsextract_cli.core.output_schema import Shape, output_schema  # noqa: E402
from wgsextract_cli.core.result_schema import result_schema  # noqa: E402
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
ROH_OUTPUT = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.roh.1.schema.json"


def roh_schema() -> dict[str, object]:
    r = roh_document
    shapes = {
        r.RohDocument: Shape("document", additional=False),
        r.Scope: Shape("scope", additional=False),
        r.PopulationFrequencies: Shape("populationFrequencies", additional=False),
        r.ImprintedRegions: Shape("imprintedRegions", additional=False),
        r.ImprintedRegion: Shape("imprintedRegion", additional=False),
        r.Condition: Shape("condition", additional=False),
        r.Markers: Shape("markers", additional=False),
        r.Parameters: Shape("parameters", additional=False),
        r.Summary: Shape("summary", additional=False),
        r.Segment: Shape("segment", additional=False),
        r.Flag: Shape("flag", additional=False),
    }
    return output_schema(
        r.RohDocument,
        shapes=shapes,
        schema_id="urn:wgsextract:schema:roh:1",
        title=r.SCHEMA_ID,
        description="qc roh: runs of homozygosity, their marker evidence, the "
        "assessed scope and F_ROH, isodisomy review flags over imprinted "
        "regions, and the provenance of the population frequencies "
        "(docs/roh.md).",
    )


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
        ROH_OUTPUT: roh_schema(),
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
