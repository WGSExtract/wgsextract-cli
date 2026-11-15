"""Generated output contracts preserve omission, nullability and closed objects."""

from typing import Literal, TypedDict

import jsonschema
import pytest

from wgsextract_cli.core.output_schema import Shape, output_schema


class Header(TypedDict):
    command: str


class Payload(Header, total=False):
    values: list[str | None]


def test_generated_output_distinguishes_missing_from_nullable():
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


def test_unmodelled_output_types_do_not_silently_widen_the_contract():
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


def test_equal_literals_in_other_generations_do_not_change_sample_schema():
    from wgsextract_cli.core.sample_document import (
        SAMPLE_SHAPES,
        SCHEMA_DESCRIPTION,
        SampleDocument,
    )

    def sample_schema():
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
