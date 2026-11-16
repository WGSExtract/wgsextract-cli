"""Inherited postponed fields must resolve in their declaring module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from wgsextract_cli.core.output_schema import Constraints

_Count = int


@dataclass
class FutureBase:
    inherited: Annotated[_Count, Constraints(minimum=0)]
