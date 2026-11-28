"""Typed command input schemas for CLI help."""

from functools import cache
from typing import Any

from wgsextract_cli.core.network_options import NETWORK_PARAMS


@cache
def _typed_commands() -> dict[str, Any]:
    from wgsextract_cli import api
    from wgsextract_cli.api._params import Params

    classes = (getattr(api, name) for name in api.__all__)
    return {
        cls.COMMAND: cls
        for cls in classes
        if isinstance(cls, type) and issubclass(cls, Params) and cls.COMMAND
    }


def input_schemas(*, include_network: bool) -> dict[str, dict[str, Any]]:
    """Map command paths to typed schemas annotated with their CLI flags."""
    from wgsextract_cli.api._params import json_schema, param_fields

    schemas = {}
    for path, cls in _typed_commands().items():
        schema = json_schema(cls, include_certification=False)
        schema.pop("$schema", None)
        for field in param_fields(cls):
            if not include_network and field.name in NETWORK_PARAMS:
                schema["properties"].pop(field.name)
                continue
            key = "x-cli-positional" if field.spec.positional else "x-cli-flag"
            schema["properties"][field.name][key] = max(field.flags, key=len)
        schemas[path] = schema
    return schemas
