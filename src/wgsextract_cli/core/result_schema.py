"""Generate result/1 from the dataclasses its real writer serializes."""

from wgsextract_cli.core.artifact_facts import ArtifactIndex
from wgsextract_cli.core.artifacts import (
    ArtifactRecord,
    ChildRun,
    Producer,
    RunRecord,
    SourceEdge,
)
from wgsextract_cli.core.errors import ResultError
from wgsextract_cli.core.output_schema import Shape, output_schema
from wgsextract_cli.core.results import (
    SCHEMA_ID,
    CommandResult,
    FileRecord,
    Fingerprint,
    Provenance,
    ReferenceRecord,
    ResultWarning,
    Scalar,
)


def result_schema() -> dict[str, object]:
    return output_schema(
        CommandResult,
        shapes={
            CommandResult: Shape("result", additional=False),
            FileRecord: Shape("file", additional=False),
            ResultWarning: Shape("warning", additional=False),
            ResultError: Shape("error", additional=False),
            Provenance: Shape("provenance", additional=False),
            ReferenceRecord: Shape("reference", additional=False),
            Scalar: Shape("scalar"),
            dict[str, Scalar]: Shape("scalars"),
            Fingerprint: Shape("fingerprint"),
            RunRecord: Shape("run", additional=False),
            ChildRun: Shape("child_run", additional=False),
            ArtifactRecord: Shape("artifact", additional=False),
            ArtifactIndex: Shape("artifact_index", additional=False),
            Producer: Shape("producer", additional=False),
            SourceEdge: Shape("source_edge", additional=False),
        },
        schema_id=f"urn:wgsextract:schema:result:{SCHEMA_ID.rsplit('/', 1)[1]}",
        title=SCHEMA_ID,
        description="What one wgsextract command read, wrote and found. "
        "Written as <output>.manifest.json next to a command's outputs and "
        "printed to stdout by --json. See docs/result-manifest.md.",
    )
