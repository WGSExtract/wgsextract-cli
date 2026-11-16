"""Dependency-free field-owner regressions, including Python 3.14 PEP 649."""

import unittest
from dataclasses import dataclass

from tests.fixtures.output_schema_future_base import FutureBase
from wgsextract_cli.core.output_schema import Shape, output_schema


@dataclass
class DeferredRecord:
    value: int


def properties(model: type) -> dict[str, object]:
    schema = output_schema(
        model,
        shapes={model: Shape("record", additional=False)},
        schema_id="urn:test:field-owner",
        title="Field ownership",
        description="Only the exact serialized Field determines its namespace.",
    )
    result = schema["properties"]
    assert isinstance(result, dict)
    return result


class DataclassOwnerTests(unittest.TestCase):
    def test_929_pep649_without_postponed_annotations_does_not_crash(self) -> None:
        self.assertEqual(properties(DeferredRecord)["value"], {"type": "integer"})

    def test_929_mixin_annotation_cannot_steal_a_dataclass_field_namespace(
        self,
    ) -> None:
        mixin = type(
            "AnnotationMixin",
            (),
            {
                "__module__": __name__,
                "__annotations__": {"inherited": str},
                "_Count": str,
            },
        )
        model: type = dataclass(
            type("MixedRecord", (mixin, FutureBase), {"__module__": __name__})
        )
        self.assertEqual(
            properties(model)["inherited"], {"type": "integer", "minimum": 0}
        )

    def test_929_diamond_uses_the_selected_field_not_a_sibling_annotation(self) -> None:
        left: type = dataclass(type("Left", (FutureBase,), {"__module__": __name__}))
        right: type = dataclass(
            type(
                "Right",
                (FutureBase,),
                {
                    "__module__": __name__,
                    "__annotations__": {"inherited": str},
                    "_Count": str,
                },
            )
        )
        diamond: type = dataclass(
            type("Diamond", (left, right), {"__module__": __name__})
        )
        self.assertEqual(
            properties(diamond)["inherited"], {"type": "integer", "minimum": 0}
        )


if __name__ == "__main__":
    unittest.main()
