"""Generate public output schemas from a deliberately small Python type subset.

Documents use TypedDict or dataclasses, not a second set of schema dictionaries.
Generation is a development operation; writers need only their type definitions.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from typing import (
    Annotated,
    Literal,
    Protocol,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

Schema = dict[str, object]


@dataclass(frozen=True)
class Constraints:
    description: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    pattern: str | None = None
    format: str | None = None
    minLength: int | None = None
    minItems: int | None = None
    uniqueItems: bool | None = None
    minProperties: int | None = None
    enum: tuple[str | int | float | bool | None, ...] | None = None
    contains_const: str | None = None


@dataclass(frozen=True)
class Shape:
    name: str
    description: str | None = None
    additional: object = True


class _TypedDictKeys(Protocol):
    __required_keys__: frozenset[str]


def _dataclass_hints(model: type) -> dict[str, object]:
    def projection() -> None:
        pass

    hints: dict[str, object] = {}
    for item in fields(model):
        # Descendants reuse Fields; annotations can be lazy or belong to a mixin.
        owner = next(
            base
            for base in reversed(model.__mro__)
            if vars(base).get("__dataclass_fields__", {}).get(item.name) is item
        )
        namespace: dict[str, object] = {
            name: value
            for base in reversed(owner.__mro__)
            for name, value in vars(base).items()
        }
        namespace[owner.__name__] = owner
        # Python 3.10 cannot resolve postponed InitVar/KW_ONLY pseudo-fields.
        annotation: object = item.type
        projection.__annotations__ = {item.name: annotation}
        resolved: dict[str, object] = get_type_hints(
            projection,
            globalns=vars(sys.modules[owner.__module__]),
            localns=namespace,
            include_extras=True,
        )
        hints[item.name] = resolved[item.name]
    return hints


def _dataclass_required(model: type) -> frozenset[str]:
    # Writers emit even defaulted fields unless omission is explicit.
    return frozenset(
        item.name for item in fields(model) if not item.metadata.get("omit_none")
    )


def output_schema(
    document: object,
    *,
    shapes: Mapping[object, Shape],
    schema_id: str,
    title: str,
    description: str,
) -> Schema:
    """Generate from TypedDict/dataclass records and supported field annotations.

    Dataclasses describe their complete ``asdict``-style field projection:
    defaults do not imply omission and factories are never called. Custom
    serializers must model their serialized shape explicitly.
    Naming is scoped to this call. Enum, tuple and Mapping fields are not
    supported; unsupported annotations fail instead of widening.
    """
    definitions: dict[str, Schema] = {}
    owners: dict[str, object] = {}

    def is_document(tp: object) -> bool:
        return is_typeddict(tp) or (isinstance(tp, type) and is_dataclass(tp))

    def claim(tp: object, spec: Shape) -> None:
        if spec.name in owners and owners[spec.name] != tp:
            raise TypeError(f"Duplicate output shape name: {spec.name!r}")
        owners[spec.name] = tp

    def describe(tp: object, *, inline: bool = False) -> Schema:
        spec = shapes.get(tp)
        if spec is not None and not is_document(tp) and not inline:
            claim(tp, spec)
            if spec.name not in definitions:
                definitions[spec.name] = describe(tp, inline=True)
            return {"$ref": f"#/$defs/{spec.name}"}
        origin: object = get_origin(tp)
        args: tuple[object, ...] = get_args(tp)
        if origin is Annotated:
            result = describe(args[0])
            for metadata in args[1:]:
                if not isinstance(metadata, Constraints):
                    raise TypeError(f"Unsupported schema metadata: {metadata!r}")
                values: dict[str, object] = vars(metadata).copy()
                contains = values.pop("contains_const")
                if contains is not None:
                    result["contains"] = {"const": contains}
                if metadata.enum is not None:
                    values = {**values, "enum": list(metadata.enum)}
                result.update(
                    {name: value for name, value in values.items() if value is not None}
                )
            return result
        if origin is Literal:
            if len(args) == 1:
                return {"const": args[0]}
            return {"enum": list(args)}
        if origin in (Union, types.UnionType):
            return {"anyOf": [describe(arg) for arg in args]}
        if origin is dict and len(args) == 2 and args[0] is str:
            return {"type": "object", "additionalProperties": describe(args[1])}
        if origin is list and len(args) == 1:
            return {"type": "array", "items": describe(args[0])}
        if tp is object:
            return {}
        primitive = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            type(None): "null",
        }
        if tp in primitive:
            return {"type": primitive[tp]}
        if is_document(tp):
            spec = shapes.get(tp)
            if spec is None:
                raise TypeError(f"Output shape needs an explicit name: {tp!r}")
            claim(tp, spec)
            if spec.name not in definitions:
                definitions[spec.name] = {}
                definitions[spec.name] = describe_object(tp, spec)
            return {"$ref": f"#/$defs/{spec.name}"}
        raise TypeError(f"Unsupported output annotation: {tp!r}")

    def describe_object(tp: object, spec: Shape) -> Schema:
        if not isinstance(tp, type):
            raise TypeError(f"Expected a TypedDict or dataclass class: {tp!r}")
        if is_dataclass(tp):
            hints = _dataclass_hints(tp)
            required = _dataclass_required(tp)
        else:
            hints = get_type_hints(tp, include_extras=True)
            required = cast(_TypedDictKeys, tp).__required_keys__
        result: Schema = {
            "type": "object",
            "properties": {name: describe(kind) for name, kind in hints.items()},
        }
        if required:
            result["required"] = [name for name in hints if name in required]
        if spec.description:
            result["description"] = spec.description
        if spec.additional is not True:
            result["additionalProperties"] = (
                spec.additional
                if spec.additional is False
                else describe(spec.additional)
            )
        return result

    if not is_document(document) or document not in shapes:
        raise TypeError(f"Expected a named output TypedDict or dataclass: {document!r}")
    claim(document, shapes[document])
    root = describe_object(document, shapes[document])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": schema_id,
        "title": title,
        "description": description,
        **root,
        "$defs": definitions,
    }
