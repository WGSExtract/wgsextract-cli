"""Shared operation state, without initializing the full execution runtime."""

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from wgsextract_cli.core.execution_context import ExecutionContext


class ConfigState:
    """One configuration: its config.toml path (None: the user's) and values."""

    def __init__(
        self,
        path: "Path | None" = None,
        values: dict[str, Any] | None = None,
        loaded: bool = False,
    ) -> None:
        self.path = path
        self.values = {} if values is None else values
        self.loaded = loaded


execution: ContextVar["ExecutionContext | None"] = ContextVar(
    "wgsextract_execution_context", default=None
)
