"""Saved settings and defaults for the configuration report."""

import logging
from collections.abc import Mapping
from typing import Any

from wgsextract_cli.core.config import get_config_path, settings


def configured_thread_count() -> int | None:
    value = settings.get("cpu_threads")
    if value is None:
        return None

    from wgsextract_cli.core.thread_count import parse_thread_count

    try:
        return parse_thread_count(value)
    except ValueError as error:
        # A bad default must not block a command or a --threads override.
        logging.warning(f"Ignoring invalid cpu_threads in {get_config_path()}: {error}")
        return None


def configuration_rows(values: Mapping[str, Any]) -> list[tuple[str, object, object]]:
    from wgsextract_cli.core.settings_schema import (
        DEV_SETTINGS,
        DEV_TABLE,
        SERVER_SETTINGS,
        SETTINGS,
    )

    rows = [(key, item.default, values.get(key)) for key, item in SETTINGS.items()]
    for table, schema in ((DEV_TABLE, DEV_SETTINGS), ("server", SERVER_SETTINGS)):
        stored = values.get(table)
        entries = stored if isinstance(stored, Mapping) else {}
        rows.extend(
            (f"{table}.{key}", item.default, entries.get(key))
            for key, item in schema.items()
        )
    return rows
