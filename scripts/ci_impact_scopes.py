"""Identify Python execution scopes for advisory test-impact reports."""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass

_Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class FunctionBody:
    first_line: int
    last_line: int
    syntax: str


@dataclass(frozen=True)
class ModuleScopes:
    initialization: tuple[tuple[str | None, str], ...]
    functions: dict[str, tuple[FunctionBody, ...]]


@dataclass(frozen=True)
class ChangedScopes:
    initialization: bool
    functions: frozenset[str]


class _Index(ast.NodeTransformer):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.functions: dict[str, list[FunctionBody]] = defaultdict(list)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
        return node

    def _function(self, node: _Function) -> _Function:
        self.scope.append(node.name)
        name = ".".join(self.scope)
        self.scope.append("<locals>")
        self.generic_visit(node)
        self.scope.pop()
        self.scope.pop()
        body = ast.Module(body=node.body, type_ignores=[])
        self.functions[name].append(
            FunctionBody(
                max(node.lineno + 1, node.body[0].lineno),
                node.end_lineno or node.lineno,
                ast.dump(body),
            )
        )
        node.body = []
        return node

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function


def _plain_declaration(node: ast.stmt) -> str | None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    if node.decorator_list or node.returns or getattr(node, "type_params", None):
        return None
    if node.args.defaults or any(
        default is not None for default in node.args.kw_defaults
    ):
        return None
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    arguments.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg)
    return node.name if all(arg.annotation is None for arg in arguments) else None


def module_scopes(source: bytes) -> ModuleScopes:
    """Separate module/class initialization and definition headers from bodies."""
    index = _Index()
    tree = ast.parse(source)
    index.visit(tree)
    return ModuleScopes(
        tuple((_plain_declaration(node), ast.dump(node)) for node in tree.body),
        {name: tuple(bodies) for name, bodies in index.functions.items()},
    )


def changed_scopes(before: ModuleScopes, after: ModuleScopes) -> ChangedScopes:
    """Match qualified names without mistaking shifted lines for code changes."""
    # A plain new/removed definition contributes a callable, not an eager
    # decorator/default dependency. Shared declaration headers still compare.
    unique = before.functions.keys() ^ after.functions.keys()
    initialization = tuple(
        statement for name, statement in before.initialization if name not in unique
    ) != tuple(
        statement for name, statement in after.initialization if name not in unique
    )
    changed: set[str] = set()
    for name in before.functions.keys() | after.functions.keys():
        left = tuple(body.syntax for body in before.functions.get(name, ()))
        right = tuple(body.syntax for body in after.functions.get(name, ()))
        if left != right:
            if max(len(left), len(right)) > 1:
                initialization = True
            else:
                changed.add(name)
    return ChangedScopes(initialization, frozenset(changed))
