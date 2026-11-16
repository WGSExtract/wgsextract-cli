"""Generated output contracts preserve omission, nullability and closed objects."""

import dataclasses
import json
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import Annotated, ClassVar, Literal, TypedDict

import jsonschema
import pytest

from wgsextract_cli.core.output_schema import Constraints, Shape, output_schema
from wgsextract_cli.core.results import FileRecord


class Header(TypedDict):
    command: str


class Payload(Header, total=False):
    values: list[str | None]


def test_generated_output_distinguishes_missing_from_nullable() -> None:
    schema = output_schema(
        Payload,
        shapes={Payload: Shape("optional_payload", additional=False)},
        schema_id="urn:test:payload",
        title="Payload",
        description="An output with an optional nullable array.",
    )
    jsonschema.validate({"command": "info"}, schema)
    jsonschema.validate({"command": "info", "values": ["found", None]}, schema)
    for document in (
        {"values": ["found"]},
        {"command": "info", "values": None},
        {"command": "info", "values": [1]},
        {"command": "info", "unexpected": "field"},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(document, schema)


def test_unmodelled_output_types_do_not_silently_widen_the_contract() -> None:
    class Unmodelled(TypedDict):
        values: set[str]

    with pytest.raises(TypeError, match="Unsupported output annotation"):
        output_schema(
            Unmodelled,
            shapes={Unmodelled: Shape("unsupported_payload")},
            schema_id="urn:test:unsupported",
            title="Unsupported",
            description="An annotation the generator does not yet support.",
        )


def test_equal_literals_in_other_generations_do_not_change_sample_schema() -> None:
    from wgsextract_cli.core.sample_document import (
        SAMPLE_SHAPES,
        SCHEMA_DESCRIPTION,
        SampleDocument,
    )

    def sample_schema() -> dict[str, object]:
        return output_schema(
            SampleDocument,
            shapes=SAMPLE_SHAPES,
            schema_id="urn:wgsextract:schema:sample:1",
            title="wgsextract.sample/1",
            description=SCHEMA_DESCRIPTION,
        )

    class OtherDocument(TypedDict):
        status: Literal["cancelled", "failed", "ok"]

    before = sample_schema()
    other = output_schema(
        OtherDocument,
        shapes={
            OtherDocument: Shape("other"),
            Literal["cancelled", "failed", "ok"]: Shape("other_status"),
        },
        schema_id="urn:test:other",
        title="Other",
        description="A separately generated document with equal literal values.",
    )
    jsonschema.validate({"status": "ok"}, other)
    assert sample_schema() == before


@dataclass
class TreeDocument:
    children: "list[TreeDocument]" = field(default_factory=list)


def test_929_dataclass_file_output_keeps_defaulted_fields_required(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("a real file\n", encoding="utf-8")
    record = FileRecord.describe(source, "input")
    schema = output_schema(
        FileRecord,
        shapes={FileRecord: Shape("file", additional=False)},
        schema_id="urn:test:file",
        title="File",
        description="A complete dataclass file record.",
    )
    document = json.loads(json.dumps(dataclasses.asdict(record)))
    jsonschema.validate(document, schema)
    for missing in ("exists", "size_bytes", "mtime", "fingerprint", "index", "metrics"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                {key: value for key, value in document.items() if key != missing},
                schema,
            )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**document, "unexpected": True}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**document, "metrics": {"count": []}}, schema)


def test_929_dataclass_inheritance_excludes_pseudofields_not_serialized_fields() -> (
    None
):
    @dataclass(frozen=True)
    class BaseRecord:
        command: str
        generation: ClassVar[int] = 1
        scratch: InitVar[str] = ""

    @dataclass(frozen=True)
    class Record(BaseRecord):
        elapsed: Annotated[float, Constraints(minimum=0)] = 0.0
        reason: str | None = None
        complete: bool = field(init=False, default=True)

    schema = output_schema(
        Record,
        shapes={Record: Shape("record", additional=False)},
        schema_id="urn:test:inherited",
        title="Inherited record",
        description="Only dataclasses.fields belong to the serialized projection.",
    )
    document = dataclasses.asdict(Record("info", scratch="not serialized"))
    jsonschema.validate(document, schema)
    for change in (
        {"elapsed": -1},
        {"complete": None},
        {"generation": 2},
        {"scratch": "not serialized"},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**document, **change}, schema)
    for missing in ("reason", "complete"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                {key: value for key, value in document.items() if key != missing},
                schema,
            )


def test_929_dataclass_generation_never_evaluates_default_factories() -> None:
    def unavailable_default() -> str:
        raise AssertionError("Generation must not instantiate runtime defaults")

    @dataclass
    class Record:
        generated: str = field(default_factory=unavailable_default)

    schema = output_schema(
        Record,
        shapes={Record: Shape("record", additional=False)},
        schema_id="urn:test:factory",
        title="Factory record",
        description="Generation does not require the writer's runtime environment.",
    )
    jsonschema.validate({"generated": "supplied by the writer"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({}, schema)


def test_929_mixed_recursive_dataclass_and_typeddict_outputs_validate() -> None:
    class Envelope(TypedDict):
        tree: TreeDocument
        payload: Payload

    @dataclass
    class Record:
        envelope: Envelope

    schema = output_schema(
        Record,
        shapes={
            Record: Shape("record", additional=False),
            Envelope: Shape("envelope", additional=False),
            TreeDocument: Shape("tree", additional=False),
            Payload: Shape("payload", additional=False),
        },
        schema_id="urn:test:mixed",
        title="Mixed records",
        description="A mixed recursive output graph.",
    )
    document = dataclasses.asdict(
        Record({"tree": TreeDocument([TreeDocument()]), "payload": {"command": "info"}})
    )
    jsonschema.validate(document, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"envelope": {"tree": {"children": [{}]}, "payload": {"command": "info"}}},
            schema,
        )


def test_929_dataclass_generation_rejects_unsupported_and_unnamed_records() -> None:
    @dataclass
    class Unmodelled:
        values: set[str]

    cases: tuple[tuple[object, dict[object, Shape], str], ...] = (
        (Unmodelled, {Unmodelled: Shape("record")}, "Unsupported output annotation"),
        (TreeDocument, {}, "Expected a named output"),
        (TreeDocument(), {}, "Expected a named output"),
    )
    for model, shapes, message in cases:
        with pytest.raises(TypeError, match=message):
            output_schema(
                model,
                shapes=shapes,
                schema_id="urn:test:unsupported",
                title="Unsupported",
                description="Fail closed rather than widening unsupported records.",
            )

    @dataclass
    class Outer:
        child: TreeDocument

    with pytest.raises(TypeError, match="needs an explicit name"):
        output_schema(
            Outer,
            shapes={Outer: Shape("outer")},
            schema_id="urn:test:unnamed",
            title="Unnamed",
            description="Every nested record needs an explicit per-generation name.",
        )
