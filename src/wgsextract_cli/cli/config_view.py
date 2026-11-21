"""Saved settings and defaults for the configuration report."""

from collections.abc import Mapping
from typing import Any

from wgsextract_cli.core.settings_schema import (
    DEV_SETTINGS,
    DEV_TABLE,
    SERVER_SETTINGS,
    SETTINGS,
)


def configuration_rows(values: Mapping[str, Any]) -> list[tuple[str, object, object]]:
    rows = [(key, item.default, values.get(key)) for key, item in SETTINGS.items()]
    for table, schema in ((DEV_TABLE, DEV_SETTINGS), ("server", SERVER_SETTINGS)):
        stored = values.get(table)
        entries = stored if isinstance(stored, Mapping) else {}
        rows.extend(
            (f"{table}.{key}", item.default, entries.get(key))
            for key, item in schema.items()
        )
    return rows
