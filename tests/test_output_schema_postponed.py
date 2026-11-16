"""Dependency-free regression runnable with Python 3.10's unittest."""

from __future__ import annotations

import dataclasses
import unittest
from dataclasses import KW_ONLY, InitVar, dataclass
from typing import Annotated, ClassVar

from tests.fixtures.output_schema_future_base import FutureBase
from wgsextract_cli.core.output_schema import Constraints, Shape, output_schema


@dataclass
class FutureRecord(FutureBase):
    lower: ClassVar[int] = 0
    _: KW_ONLY
    label: str
    setup: InitVar[str] = ""
    note: str | None = None
    count: Annotated[int, Constraints(minimum=lower)] = 0


class PostponedDataclassTests(unittest.TestCase):
    def test_929_python310_skips_postponed_initvar_and_kw_only(self) -> None:
        record = FutureRecord(3, label="complete", setup="not serialized")
        document = dataclasses.asdict(record)
        schema = output_schema(
            FutureRecord,
            shapes={FutureRecord: Shape("future", additional=False)},
            schema_id="urn:test:postponed",
            title="Postponed record",
            description="Only real fields are evaluated, in their original namespaces.",
        )
        properties = schema["properties"]
        required = schema["required"]
        assert isinstance(properties, dict)
        assert isinstance(required, list)
        self.assertEqual(set(properties), set(document))
        self.assertEqual(set(required), set(document))
        self.assertEqual(properties["inherited"], {"type": "integer", "minimum": 0})
        self.assertEqual(properties["label"], {"type": "string"})
        self.assertEqual(properties["count"], {"type": "integer", "minimum": 0})
        self.assertEqual(
            properties["note"], {"anyOf": [{"type": "string"}, {"type": "null"}]}
        )


if __name__ == "__main__":
    unittest.main()
