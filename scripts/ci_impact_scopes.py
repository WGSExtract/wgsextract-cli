"""Identify Python execution scopes for advisory test-impact reports."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import PurePosixPath

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


def decisive_module_scope(
    before: bytes, after: bytes, old: ModuleScopes, new: ModuleScopes
) -> bool:
    """Preserve the historical line-based module arm in the decisive baseline."""
    scopes = []
    for index in (old, new):
        scopes.append(
            {
                line: name
                for name, bodies in index.functions.items()
                for body in bodies
                for line in range(body.first_line, body.last_line + 1)
            }
        )
    common = old.functions.keys() & new.functions.keys()
    diff = SequenceMatcher(
        None, before.splitlines(), after.splitlines(), autojunk=False
    )
    return any(
        scope.get(line + 1) not in common
        for tag, a, b, c, d in diff.get_opcodes()
        if tag != "equal"
        for scope, lines in zip(scopes, (range(a, b), range(c, d)), strict=True)
        for line in lines
    )


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


def _plain_declaration(node: ast.stmt, *, postponed_annotations: bool) -> str | None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    if node.decorator_list or getattr(node, "type_params", None):
        return None
    if node.args.defaults or any(
        default is not None for default in node.args.kw_defaults
    ):
        return None
    if postponed_annotations:
        return node.name
    if node.returns:
        return None
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    arguments.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg)
    return node.name if all(arg.annotation is None for arg in arguments) else None


def module_scopes(source: bytes) -> ModuleScopes:
    """Separate module/class initialization and definition headers from bodies."""
    index = _Index()
    tree = ast.parse(source)
    postponed_annotations = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )
    index.visit(tree)
    return ModuleScopes(
        tuple(
            (
                _plain_declaration(node, postponed_annotations=postponed_annotations),
                ast.dump(node),
            )
            for node in tree.body
        ),
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


def module_contract_tests(
    path: str, tests: Iterable[str], declarations: Mapping[str, Iterable[str]]
) -> set[str]:
    """Select primary contract owners, without following every module user."""
    module = PurePosixPath(path).stem
    conventional = f"test_{module.removeprefix('_')}.py"
    declared = set(declarations.get(path, ()))
    return {
        test
        for test in tests
        if test in declared
        or (module != "__init__" and PurePosixPath(test).name == conventional)
    }


def shadow_candidates(
    shared: Iterable[str],
    module_users: Mapping[str, Iterable[str]],
    scopes: Mapping[str, ChangedScopes],
    tests: Iterable[str],
    declarations: Mapping[str, Iterable[str]],
    *,
    symbol_module_users: Mapping[str, Iterable[str]] | None = None,
    widely_called_shared: Iterable[str] | None = None,
) -> dict[str, list[str]]:
    """Compare variants without changing the authoritative selection.

    ``shared`` includes the function, caller, fixture, resource and universal
    rules. Missing AST evidence retains that path's original module rule.
    """
    decisive = set(shared)
    tight = set(decisive)
    symbol = set(decisive)
    widely_called = set(widely_called_shared or ())
    universe = set(tests)
    for path, users in module_users.items():
        candidates = set(users)
        decisive.update(candidates)
        if path not in scopes or scopes[path].initialization:
            tight.update(candidates)
        if symbol_module_users is not None:
            scoped = set(symbol_module_users.get(path, candidates))
            symbol.update(scoped)
            widely_called.update(scoped)
    for path, changed in scopes.items():
        if path.startswith("src/") and changed.functions and not changed.initialization:
            contracts = module_contract_tests(path, universe, declarations)
            tight.update(contracts)
            symbol.update(contracts)
            widely_called.update(contracts)
    variants = {"decisive": decisive, "tight": tight}
    if symbol_module_users is not None:
        variants["symbol"] = symbol
        if widely_called_shared is not None:
            variants["widely_called"] = widely_called
    return {name: sorted(targets & universe) for name, targets in variants.items()}
