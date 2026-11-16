"""Code-first inputs for the three read-only HTTP/MCP lookups.

None defaults mean omission, not wire-level null. The parser validates provided
values before constructing the typed input used by the lookup.
"""

from __future__ import annotations

import types
from collections.abc import Callable, Mapping
from dataclasses import MISSING, dataclass, fields
from typing import (
    Annotated,
    Literal,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from wgsextract_cli.core.personal_report import SECTIONS
from wgsextract_cli.core.variant_table import COLUMN_NAMES

from .operations import RequestError

MAX_ROWS = 1000
CONFIDENCE_RANK = {"low": 0, "moderate": 1, "high": 2}
InputT = TypeVar("InputT", bound="LookupInputs")
Kind = Literal["string", "boolean", "integer", "array"]


@dataclass(frozen=True)
class InputSpec:
    description: str | None = None
    choices: tuple[str, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class InputField:
    name: str
    kind: Kind
    required: bool
    spec: InputSpec

    def schema(self) -> dict[str, object]:
        result: dict[str, object] = {"type": self.kind}
        target = result
        if self.kind == "array":
            target = {"type": "string"}
            result["items"] = target
        if self.spec.choices is not None:
            target["enum"] = list(self.spec.choices)
        if self.spec.description is not None:
            result["description"] = self.spec.description
        if self.spec.minimum is not None:
            result["minimum"] = self.spec.minimum
        if self.spec.maximum is not None:
            result["maximum"] = self.spec.maximum
        return result

    def validate(self, value: object) -> None:
        valid = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "array": isinstance(value, list) and all(isinstance(v, str) for v in value),
        }[self.kind]
        if not valid:
            raise RequestError(
                400,
                "usage",
                f"{self.name} must be a{'n' * (self.kind[0] in 'ai')} {self.kind}.",
            )
        choices = self.spec.choices
        if choices is not None:
            if self.kind == "array" and isinstance(value, list):
                bad = [v for v in value if v not in choices]
                if bad:
                    raise RequestError(
                        400, "usage", f"Unknown {self.name}: {', '.join(bad)}."
                    )
            elif value not in choices:
                raise RequestError(
                    400, "usage", f"{self.name} must be one of {', '.join(choices)}."
                )
        if isinstance(value, int) and (
            (self.spec.minimum is not None and value < self.spec.minimum)
            or (self.spec.maximum is not None and value > self.spec.maximum)
        ):
            raise RequestError(
                400,
                "usage",
                f"{self.name} must be {self.spec.minimum} to {self.spec.maximum}.",
            )


@dataclass(frozen=True, kw_only=True)
class LookupInputs:
    path: Annotated[str, InputSpec(description="Absolute path in an allowed folder.")]

    @classmethod
    def properties(cls) -> dict[str, dict[str, object]]:
        return {item.name: item.schema() for item in input_fields(cls)}

    @classmethod
    def required(cls) -> tuple[str, ...]:
        return tuple(item.name for item in input_fields(cls) if item.required)

    @classmethod
    def schema(cls) -> dict[str, object]:
        return {
            "type": "object",
            "properties": cls.properties(),
            "required": list(cls.required()),
            "additionalProperties": False,
        }

    @classmethod
    def parse(cls: type[InputT], arguments: Mapping[str, object]) -> InputT:
        bindings = {item.name: item for item in input_fields(cls)}
        unknown = set(arguments) - bindings.keys()
        if unknown:
            raise RequestError(
                400, "usage", f"Unknown parameter(s): {', '.join(sorted(unknown))}."
            )
        for item in bindings.values():
            if item.required and arguments.get(item.name) in (None, ""):
                raise RequestError(400, "usage", f"{item.name} is required.")
        for name, value in arguments.items():
            bindings[name].validate(value)
        # A concrete dataclass's keyword signature is only known at runtime.
        construct = cast(Callable[..., InputT], cls)
        return construct(**arguments)


def input_fields(model: type[LookupInputs]) -> tuple[InputField, ...]:
    hints: dict[str, object] = get_type_hints(model, include_extras=True)
    result = []
    kinds: dict[object, Kind] = {
        str: "string",
        bool: "boolean",
        int: "integer",
        list[str]: "array",
    }
    for item in fields(model):
        annotation = hints[item.name]
        spec = InputSpec()
        if get_origin(annotation) is Annotated:
            annotation, *metadata = get_args(annotation)
            if len(metadata) != 1 or not isinstance(metadata[0], InputSpec):
                raise TypeError(f"Unsupported lookup metadata: {metadata!r}")
            spec = metadata[0]
        if get_origin(annotation) in (Union, types.UnionType):
            args = get_args(annotation)
            nonnull = [arg for arg in args if arg is not type(None)]
            if item.default is not None or len(nonnull) != 1 or type(None) not in args:
                raise TypeError(f"Unsupported lookup union: {annotation!r}")
            annotation = nonnull[0]
        kind = kinds.get(annotation)
        if kind is None:
            raise TypeError(f"Unsupported lookup annotation: {annotation!r}")
        if (spec.minimum is not None or spec.maximum is not None) and kind != "integer":
            raise TypeError(f"Numeric constraints on non-integer field: {item.name}")
        if spec.choices is not None and kind not in ("string", "array"):
            raise TypeError(f"String choices on non-string field: {item.name}")
        result.append(InputField(item.name, kind, item.default is MISSING, spec))
    return tuple(result)


@dataclass(frozen=True, kw_only=True)
class ManifestInputs(LookupInputs):
    full: Annotated[
        bool | None, InputSpec(description="Include provenance (tools, params).")
    ] = None


@dataclass(frozen=True, kw_only=True)
class VariantInputs(LookupInputs):
    gene: Annotated[str | None, InputSpec(description="Gene symbol (BRCA1).")] = None
    rsid: Annotated[str | None, InputSpec(description="dbSNP rsID (rs429358).")] = None
    region: Annotated[
        str | None,
        InputSpec(
            description="chrom, chrom:pos or chrom:start-end, named as in the table."
        ),
    ] = None
    clinvar: Annotated[
        str | None,
        InputSpec(
            description="A ClinVar significance term, e.g. Pathogenic or Likely_pathogenic (case-insensitive)."
        ),
    ] = None
    min_confidence: Annotated[
        str | None,
        InputSpec(
            choices=tuple(CONFIDENCE_RANK),
            description="Only calls labelled at least this confident.",
        ),
    ] = None
    columns: Annotated[
        list[str] | None,
        InputSpec(
            choices=tuple(COLUMN_NAMES),
            description="Columns to return (default: all non-empty).",
        ),
    ] = None
    limit: Annotated[int | None, InputSpec(minimum=1, maximum=MAX_ROWS)] = None
    cursor: Annotated[
        str | None, InputSpec(description="next_cursor from the previous page.")
    ] = None


@dataclass(frozen=True, kw_only=True)
class ReportInputs(LookupInputs):
    path: Annotated[
        str,
        InputSpec(
            description="The sample folder, or its *_personal_report.json, in an allowed folder."
        ),
    ]
    section: Annotated[
        str | None,
        InputSpec(choices=tuple(SECTIONS), description="Return this section in full."),
    ] = None
