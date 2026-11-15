"""Generated output contracts preserve omission, nullability and closed objects."""

from typing import TypedDict

import jsonschema
import pytest

from wgsextract_cli.core.output_schema import output_schema, shape


@shape("required_header")
class Header(TypedDict):
    command: str


@shape("optional_payload", additional=False)
class Payload(Header, total=False):
    values: list[str | None]


def test_generated_output_distinguishes_missing_from_nullable():
    schema = output_schema(
        Payload,
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
    @shape("unsupported_payload")
    class Unmodelled(TypedDict):
        values: set[str]

    with pytest.raises(TypeError, match="Unsupported output annotation"):
        output_schema(
            Unmodelled,
            schema_id="urn:test:unsupported",
            title="Unsupported",
            description="An annotation the generator does not yet support.",
        )
