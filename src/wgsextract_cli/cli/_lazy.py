"""Execution-only imports for parser registration."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wgsextract_cli.api._params import Params


class _CommandHandler:
    def __init__(
        self,
        module: str,
        name: str,
        params: type[Params] | None,
        render: dict[str, str],
    ) -> None:
        self._module, self._name = module, name
        self._params, self._render = params, render
        self.__module__ = module
        self.__name__ = self.__qualname__ = name

    @property
    def __wrapped__(self) -> Callable[[argparse.Namespace], None]:
        """Resolve on execution or explicit introspection (inspect.unwrap)."""
        target: Callable[[argparse.Namespace], None] = getattr(
            import_module(self._module), self._name
        )
        if self._params is not None:
            from wgsextract_cli.api._cli import cli_handler

            target = cli_handler(self._params, target, **self._render)
        return target

    def __call__(self, args: argparse.Namespace) -> None:
        self.__wrapped__(args)


def command_handler(
    module: str,
    name: str,
    params: type[Params] | None = None,
    **render: str,
) -> Callable[[argparse.Namespace], None]:
    """Resolve a command only when invoked, after parsing and preflight."""

    return _CommandHandler(module, name, params, render)
