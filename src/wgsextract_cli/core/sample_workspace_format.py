"""Read, validate, and render the on-disk sample workspace document."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import tomli_w

from wgsextract_cli.core.sample_state import WorkspaceState
from wgsextract_cli.core.sample_types import SCHEMA_ID

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

__all__ = [
    "SCHEMA_RESOURCE",
    "WORKSPACE_FILE",
    "load_schema",
    "read_workspace",
    "render",
    "tomllib",
]

SCHEMA_RESOURCE = "assets/schemas/wgsextract.sample.1.schema.json"
WORKSPACE_FILE = "wgsextract.sample.toml"
_SCHEMA_PREFIX = "wgsextract.sample/"
_SCHEMA_VERSION = 1
_HEADER = (
    "# WGS Extract sample workspace (wgsextract.sample/1); "
    "see docs/sample-workspace.md.\n"
    "# [override] holds your corrections and wins over detection: edit it here\n"
    "# or with 'wgsextract sample set'. wgsextract rewrites [detected] and\n"
    "# [outputs]; comments are not kept.\n\n"
)


def read_workspace(path: Path) -> WorkspaceState:
    try:
        with open(path, "rb") as stream:
            raw: dict[str, object] = tomllib.load(stream)
    except FileNotFoundError:
        return WorkspaceState({"schema": SCHEMA_ID})
    except OSError as e:
        return WorkspaceState({}, True, False, f"unreadable: {e}")
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return WorkspaceState({}, True, False, f"not valid TOML: {e}")
    schema = raw.get("schema")
    if schema == SCHEMA_ID:
        malformed = _malformed(raw)
        if malformed:
            return WorkspaceState({}, True, False, malformed)
        return WorkspaceState(raw, True)
    version = None
    if isinstance(schema, str) and schema.startswith(_SCHEMA_PREFIX):
        with contextlib.suppress(ValueError):
            version = int(schema.removeprefix(_SCHEMA_PREFIX))
    if schema is None or (version is not None and version < _SCHEMA_VERSION):
        # An older layout: keep the user's overrides and other keys, but
        # detect everything again.
        raw.pop("detected", None)
        raw["schema"] = SCHEMA_ID
        return WorkspaceState(raw, True, True, f"older schema {schema!r}; upgraded")
    if version is not None:
        return WorkspaceState(
            {}, True, False, f"written by a newer wgsextract ({schema})"
        )
    return WorkspaceState({}, True, False, f"unknown schema {schema!r}")


def _malformed(raw: Mapping[str, object]) -> str | None:
    """What makes a current-schema document unusable, if anything."""
    for section in ("override", "detected", "outputs"):
        table = raw.get(section, {})
        if not isinstance(table, dict):
            return f"[{section}] isn't a table"
        if section == "override" and not all(
            isinstance(v, str) or (isinstance(v, dict) and "fingerprint" in v)
            for v in table.values()
        ):
            return "an entry of [override] is neither a value nor a file's table"
        if section != "override" and not all(
            isinstance(entry, dict) for entry in table.values()
        ):
            return f"an entry of [{section}] isn't a table"
    return None


def render(raw: Mapping[str, object]) -> str:
    """The TOML text: schema first, then [override], [detected], [outputs]."""
    order = ("schema", "override", "detected", "outputs")
    ordered = {k: raw[k] for k in order if k in raw}
    ordered.update({k: v for k, v in raw.items() if k not in order})
    text: str = tomli_w.dumps(ordered)
    return _HEADER + text


def load_schema() -> dict[str, Any]:
    """The wgsextract.sample/1 JSON Schema shipped with the package."""
    import json
    from importlib import resources

    text = resources.files("wgsextract_cli").joinpath(SCHEMA_RESOURCE).read_text()
    schema: dict[str, Any] = json.loads(text)
    return schema
