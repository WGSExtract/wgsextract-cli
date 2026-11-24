"""Resolve the folders a job uses for write conflict checks."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from .operations import Operation, is_network_path

# In precedence order: a trio's proband overrides its other inputs.
INPUT_NAMES = ("proband", "vcf_input", "input", "inputs", "r1")


def folder_key(folder: str) -> str:
    folder = os.path.expanduser(folder)
    # Requests were checked, but never open a network path (see PathGuard).
    if not is_network_path(folder):
        folder = os.path.realpath(folder)
    return os.path.normcase(folder)


def beside_folder(op: Operation, params: Mapping[str, Any]) -> str:
    """The folder an operation writes in beside its ``WRITES_BESIDE`` path:
    the path's own when it is a folder."""
    value = beside_path(op, params)
    return value if is_local_folder(value) else os.path.dirname(value)


def beside_path(op: Operation, params: Mapping[str, Any]) -> str:
    """An operation's ``WRITES_BESIDE`` path: the request's, else the
    configured one."""
    from wgsextract_cli.core.config import settings

    name = op.params.WRITES_BESIDE
    value = params.get(name)
    if not value:
        item = next((f for f in op.fields() if f.name == name), None)
        key = item.spec.config_key if item else None
        value = settings.get(key) if key else None
    return value if isinstance(value, str) else ""


def is_local_folder(value: str) -> bool:
    return (
        bool(value)
        and not is_network_path(value)
        and os.path.isdir(os.path.expanduser(value))
    )


def first_input(params: Mapping[str, Any]) -> str:
    """The input a job's outputs go beside when it names no folder."""
    for name in INPUT_NAMES:
        value = params.get(name)
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, str) and value:
            return value
    return ""
