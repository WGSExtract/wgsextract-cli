"""Generate public output schemas from a deliberately small Python type subset.

Dictionary documents use TypedDict, not a second set of schema dictionaries.
Generation is a development operation; writers need only their type definitions.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from dataclasses import dataclass
from typing import (
    Annotated,
    Literal,
    Protocol,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

T = TypeVar("T")
Schema = dict[str, object]


@dataclass(frozen=True)
class Constraints:
    description: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    pattern: str | None = None
    format: str | None = None


@dataclass(frozen=True)
class Shape:
    name: str
    description: str | None = None
    additional: object = True


_SHAPES: dict[object, Shape] = {}


class _TypedDictKeys(Protocol):
    __required_keys__: frozenset[str]


def shape(
    name: str, *, description: str | None = None, additional: object = True
) -> Callable[[T], T]:
    """Name a reusable type and declare an object's extension-key policy."""

    def decorate(cls: T) -> T:
        spec = Shape(name, description, additional)
        previous = _SHAPES.get(cls)
        if previous is not None and previous != spec:
            raise TypeError(f"Output type already has a different shape: {cls!r}")
        _SHAPES[cls] = spec
        return cls

    return decorate


def output_schema(
    document: object, *, schema_id: str, title: str, description: str
) -> Schema:
    """JSON Schema 2020-12; reject unmodelled annotations rather than widening."""
    definitions: dict[str, Schema] = {}
    owners: dict[str, object] = {}

    def claim(tp: object, spec: Shape) -> None:
        if spec.name in owners and owners[spec.name] != tp:
            raise TypeError(f"Duplicate output shape name: {spec.name!r}")
        owners[spec.name] = tp

    def describe(tp: object, *, inline: bool = False) -> Schema:
        spec = _SHAPES.get(tp)
        if spec is not None and not is_typeddict(tp) and not inline:
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
                values: dict[str, object] = vars(metadata)
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
        if is_typeddict(tp):
            spec = _SHAPES.get(tp)
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
            raise TypeError(f"Expected a TypedDict class: {tp!r}")
        hints: dict[str, object] = get_type_hints(tp, include_extras=True)
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

    if not is_typeddict(document) or document not in _SHAPES:
        raise TypeError(f"Expected a named output TypedDict: {document!r}")
    claim(document, _SHAPES[document])
    root = describe_object(document, _SHAPES[document])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": schema_id,
        "title": title,
        "description": description,
        **root,
        "$defs": definitions,
    }
