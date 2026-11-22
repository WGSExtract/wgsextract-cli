"""Flatten generated output schemas and compare them with a pinned baseline.

A schema's *surface* maps each consumer-visible path (``inputs[].role``,
``metrics.*``) to the JSON values a reader may see there. Comparisons follow
the reader's side of the contract: a producer may narrow what it writes, but
must not widen, drop or reinterpret a stable value.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from typing import Any

TIERS = ("stable", "experimental", "internal")
SUPPORTED = {
    "$schema",
    "$id",
    "$ref",
    "$defs",
    "title",
    "description",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "anyOf",
    "const",
    "enum",
    "format",
    "pattern",
    "minimum",
    "maximum",
    "minLength",
    "default",
    "examples",
}
JSON_TYPES = ("array", "boolean", "integer", "null", "number", "object", "string")


@dataclass(frozen=True)
class Finding:
    level: str  # error, notice, approved or info
    code: str
    document: str
    path: str
    release: str
    message: str

    def key(self) -> str:
        return f"{self.document}:{self.path}"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _value_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


class _Flattener:
    def __init__(self, schema: Mapping[str, Any]) -> None:
        self.defs: Mapping[str, Any] = schema.get("$defs", {})
        self.paths: dict[str, dict[str, Any]] = {}

    def alternatives(
        self, node: Mapping[str, Any], stack: tuple[str, ...]
    ) -> list[tuple[Mapping[str, Any], tuple[str, ...]]]:
        unknown = set(node) - SUPPORTED
        if unknown:
            # Fail closed: an unmodelled keyword could hide a contract change.
            raise ValueError(f"Unsupported schema keywords: {sorted(unknown)}")
        rest = {
            key: value for key, value in node.items() if key not in {"$ref", "anyOf"}
        }
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            if name not in self.defs:
                raise ValueError(f"Unresolved schema reference {node['$ref']}")
            if name in stack:
                target = self.defs[name]
                return [({"type": target.get("type", "object"), **rest}, stack)]
            return self.alternatives({**self.defs[name], **rest}, (*stack, name))
        if "anyOf" in node:
            return [
                pair
                for option in node["anyOf"]
                for pair in self.alternatives({**rest, **option}, stack)
            ]
        return [(node, stack)]

    def visit(
        self,
        path: str,
        node: Mapping[str, Any],
        stack: tuple[str, ...],
        *,
        required: bool,
    ) -> None:
        options = self.alternatives(node, stack)
        self.paths[path] = describe([option for option, _ in options], required)
        objects = [
            (option, where)
            for option, where in options
            if "properties" in option or "additionalProperties" in option
        ]
        names: dict[str, list[tuple[Mapping[str, Any], tuple[str, ...]]]] = {}
        for option, where in objects:
            for name, child in option.get("properties", {}).items():
                names.setdefault(name, []).append(({**child}, where))
        for name, children in names.items():
            always = all(name in option.get("required", ()) for option, _ in objects)
            self.visit_many(join(path, name), children, required=always)
        maps = [
            (option["additionalProperties"], where)
            for option, where in objects
            if isinstance(option.get("additionalProperties"), dict)
        ]
        if maps:
            self.visit_many(join(path, "*"), maps, required=False)
        items = [
            (option["items"], where) for option, where in options if "items" in option
        ]
        if items:
            self.visit_many(f"{path}[]", items, required=True)

    def visit_many(
        self,
        path: str,
        nodes: list[tuple[Mapping[str, Any], tuple[str, ...]]],
        *,
        required: bool,
    ) -> None:
        if len(nodes) == 1:
            self.visit(path, nodes[0][0], nodes[0][1], required=required)
            return
        # Merge differently shaped alternatives (e.g. sample facts) as anyOf.
        merged: dict[str, Any] = {"anyOf": [node for node, _ in nodes]}
        self.visit(path, merged, nodes[0][1], required=required)


def join(parent: str, name: str) -> str:
    return f"{parent}.{name}" if parent else name


def describe(options: list[Mapping[str, Any]], required: bool) -> dict[str, Any]:
    """The values a reader may see at one path, merged over alternatives."""
    types: set[str] = set()
    values: list[object] | None = []
    formats: set[str | None] = set()
    patterns: set[str | None] = set()
    minimums: list[float | None] = []
    maximums: list[float | None] = []
    for option in options:
        if "const" in option or "enum" in option:
            choices = [option["const"]] if "const" in option else option["enum"]
            declared = option.get("type")
            types.update(
                [declared] if isinstance(declared, str) else map(_value_type, choices)
            )
            if values is not None:
                values.extend(choice for choice in choices if choice is not None)
            continue
        declared = option.get("type")
        option_types = (
            [declared]
            if isinstance(declared, str)
            else list(declared)
            if isinstance(declared, list)
            else ["object"]
            if "properties" in option or "additionalProperties" in option
            else ["array"]
            if "items" in option
            else list(JSON_TYPES)
        )
        types.update(option_types)
        if option_types != ["null"]:
            values = None
        if "string" in option_types:
            formats.add(option.get("format"))
            patterns.add(option.get("pattern"))
        if {"integer", "number"} & set(option_types):
            minimums.append(option.get("minimum"))
            maximums.append(option.get("maximum"))
    record: dict[str, Any] = {"types": sorted(types), "required": required}
    if values:
        record["values"] = sorted({json.dumps(value) for value in values})
    for key, seen in (("format", formats), ("pattern", patterns)):
        if len(seen) == 1 and None not in seen:
            record[key] = next(iter(seen))
    if minimums and None not in minimums:
        record["minimum"] = min(m for m in minimums if m is not None)
    if maximums and None not in maximums:
        record["maximum"] = max(m for m in maximums if m is not None)
    return record


def flatten(schema: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Map every consumer-visible path of a schema to its value domain."""
    flattener = _Flattener(schema)
    if "type" in schema or "properties" in schema:
        root = {key: value for key, value in schema.items() if key != "$defs"}
        flattener.visit("", root, (), required=True)
    else:
        # A definitions-only document (HTTP responses): each definition is a root.
        for name, node in schema.get("$defs", {}).items():
            flattener.visit(name, node, (name,), required=True)
    flattener.paths.pop("", None)
    return dict(sorted(flattener.paths.items()))


def segments(path: str) -> list[str]:
    return path.replace("[]", ".[]").split(".")


def ancestors(path: str) -> Iterator[str]:
    """Proper prefixes of a path that are paths themselves, nearest first."""
    for index in range(len(path) - 1, 0, -1):
        if path[index] == "." or path.startswith("[]", index):
            yield path[:index]


def covers(prefix: str, path: str) -> bool:
    return path == prefix or path.startswith((f"{prefix}.", f"{prefix}[]"))


def classify(
    paths: Mapping[str, Mapping[str, Any]],
    tier: str,
    fields: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Attach the tier, meaning and scientific flag of each path."""
    keys = sorted(fields, key=len, reverse=True)
    classified = {}
    for path, node in paths.items():
        owners = [key for key in keys if covers(key, path)]
        effective = next(
            (fields[key]["tier"] for key in owners if "tier" in fields[key]), tier
        )
        record = {**node, "tier": effective}
        if any(fields[key].get("scientific") for key in owners):
            record["scientific"] = True
        if path in fields and "meaning" in fields[path]:
            record["meaning"] = " ".join(fields[path]["meaning"].split())
        classified[path] = record
    return classified


def _narrower(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    """Why the current domain is not within the baseline's (empty if it is)."""
    problems = []
    allowed = set(baseline["types"])
    if "number" in allowed:
        allowed.add("integer")
    widened = sorted(set(current["types"]) - allowed)
    if widened:
        problems.append(
            f"now also {', '.join(widened)} (was {', '.join(baseline['types'])})"
        )
    if "values" in baseline:
        extra = sorted(set(current.get("values", ["<any>"])) - set(baseline["values"]))
        if extra:
            problems.append(f"new values {', '.join(extra)}")
    for key in ("format", "pattern"):
        if key in baseline and current.get(key) != baseline[key]:
            problems.append(f"{key} {current.get(key)!r} (was {baseline[key]!r})")
    if (
        "minimum" in baseline
        and current.get("minimum", float("-inf")) < baseline["minimum"]
    ):
        problems.append(f"minimum {current.get('minimum')} (was {baseline['minimum']})")
    if (
        "maximum" in baseline
        and current.get("maximum", float("inf")) > baseline["maximum"]
    ):
        problems.append(f"maximum {current.get('maximum')} (was {baseline['maximum']})")
    return problems


ACTIONS = {
    "stable": "Restore it, or follow the breaking-change procedure in "
    "docs/output-contracts.md (a new schema major version, or an owner-approved "
    "entry in contracts/output-contracts.json approved_breaks).",
    "experimental": "Announce it: name `{key}` in docs/release-notes/unreleased.md.",
}


def compare(
    document: str,
    current: Mapping[str, Mapping[str, Any]],
    baseline: Mapping[str, Mapping[str, Any]],
    release: str,
) -> Iterator[Finding]:
    """Findings for one document's classified surface against a baseline's."""
    for path, before in baseline.items():
        tier = before["tier"]
        if tier == "internal":
            continue
        level = "error" if tier == "stable" else "notice"
        after = current.get(path)
        problems: list[tuple[str, str]] = []
        if after is None:
            problems.append(("field_removed", "was removed"))
        else:
            if after["tier"] != "stable" and tier == "stable":
                problems.append(("tier_downgraded", f"was demoted to {after['tier']}"))
            narrower = _narrower(after, before)
            if narrower:
                code = (
                    "values_widened"
                    if all(p.startswith("new values") for p in narrower)
                    else "type_changed"
                )
                problems.append((code, "changed type: " + "; ".join(narrower)))
            if before["required"] and not after["required"]:
                problems.append(("requiredness_changed", "is no longer always present"))
            if "meaning" in before and after.get("meaning") != before["meaning"]:
                problems.append(
                    (
                        "meaning_changed",
                        f"changed meaning from {before['meaning']!r} to {after.get('meaning')!r}",
                    )
                )
        for code, what in problems:
            key = f"{document}:{path}"
            yield Finding(
                level,
                f"{tier}_{code}",
                document,
                path,
                release,
                f"{tier.capitalize()} `{key}` {what} since {release}. "
                + ACTIONS[tier].format(key=key),
            )
    for path, after in current.items():
        if path not in baseline and after["tier"] != "internal":
            yield Finding(
                "info",
                "field_added",
                document,
                path,
                release,
                f"`{document}:{path}` is new since {release} ({after['tier']}); "
                "additions are compatible.",
            )


def _values_at(instance: object, parts: list[str]) -> Iterator[object]:
    if not parts:
        yield instance
        return
    head, rest = parts[0], parts[1:]
    if head == "[]":
        if isinstance(instance, list):
            for item in instance:
                yield from _values_at(item, rest)
    elif head == "*":
        if isinstance(instance, dict):
            for value in instance.values():
                yield from _values_at(value, rest)
    elif isinstance(instance, dict) and head in instance:
        yield from _values_at(instance[head], rest)


def check_instance(
    document: str,
    instance: Mapping[str, Any],
    baseline: Mapping[str, Any],
    release: str,
) -> list[Finding]:
    """Check a real output document against a baseline's stable surface."""
    findings: list[Finding] = []
    paths: Mapping[str, Mapping[str, Any]] = baseline["documents"][document]["paths"]
    for path, node in paths.items():
        if node["tier"] != "stable":
            continue
        parts = segments(path)
        name = parts[-1]
        if name in {"[]", "*"}:
            values = list(_values_at(instance, parts))
        else:
            values = []
            for parent in _values_at(instance, parts[:-1]):
                if not isinstance(parent, dict):
                    continue
                if name in parent:
                    values.append(parent[name])
                elif node["required"]:
                    findings.append(
                        Finding(
                            "error",
                            "stable_field_missing_in_output",
                            document,
                            path,
                            release,
                            f"Output lacks stable `{document}:{path}` (required since {release}).",
                        )
                    )
        for value in values:
            allowed = set(node["types"]) | (
                {"integer"} if "number" in node["types"] else set()
            )
            seen = _value_type(value)
            wrong = (
                seen not in allowed
                or (
                    "values" in node
                    and value is not None
                    and json.dumps(value) not in node["values"]
                )
                or (
                    "pattern" in node
                    and isinstance(value, str)
                    and not re.search(node["pattern"], value)
                )
            )
            if wrong:
                findings.append(
                    Finding(
                        "error",
                        "stable_type_mismatch_in_output",
                        document,
                        path,
                        release,
                        f"Output `{document}:{path}` is {json.dumps(value)[:60]}, outside "
                        f"its {release} contract {node['types']}.",
                    )
                )
    return findings
