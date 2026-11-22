"""Classify import-time AST deltas for symbol-level advisory selection."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_IMPORTS = (ast.Import, ast.ImportFrom)
_HOOKS = {"__all__", "__getattr__", "__init_subclass__", "__set_name__"}

# These contract suites are named after the workflow they exercise, not the
# implementation module that owns the shared behavior.
SYMBOL_CONTRACTS = {
    "src/wgsextract_cli/core/chunked_alignment.py": (
        "tests/test_chunked_realign.py",
        "tests/test_chunked_realign_resume.py",
    ),
    "src/wgsextract_cli/commands/qc/_relate_vcf.py": ("tests/test_somalier_relate.py",),
}


@dataclass
class SymbolDelta:
    imports: bool = False
    broad: bool = False
    symbols: set[str] = field(default_factory=set)


def _side_effect(node: ast.AST) -> bool:
    return any(
        isinstance(
            child, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr)
        )
        for child in ast.walk(node)
    )


def _key(node: ast.stmt) -> str | None:
    if isinstance(node, (*_DEFS, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id
    if type(node).__name__ == "TypeAlias":
        alias = getattr(node, "name", None)
        if isinstance(alias, ast.Name):
            return alias.id
    return None


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _structure(node: ast.AST) -> str:
    if isinstance(node, (*_DEFS, ast.ClassDef)):
        header = [
            ast.dump(child)
            for child in ast.iter_child_nodes(node)
            if child not in node.body
        ]
        if isinstance(node, ast.ClassDef):
            header.extend(
                _structure(child) for child in node.body if not _is_docstring(child)
            )
        return repr((type(node).__name__, node.name, header))
    return ast.dump(node)


def _unsafe(node: ast.stmt, peer: ast.stmt | None) -> bool:
    if isinstance(node, (*_DEFS, ast.ClassDef)):
        peer_decorators = (
            peer.decorator_list if isinstance(peer, (*_DEFS, ast.ClassDef)) else []
        )
        if [ast.dump(d) for d in node.decorator_list] != [
            ast.dump(d) for d in peer_decorators
        ]:
            return bool(node.decorator_list)
        if isinstance(node, ast.ClassDef):
            if node.keywords or any(_side_effect(base) for base in node.bases):
                return True
            members = [item for item in node.body if not _is_docstring(item)]
            before = (
                {name: member for member in peer.body if (name := _key(member))}
                if isinstance(peer, ast.ClassDef)
                else {}
            )
            for item in members:
                key = _key(item)
                if key is None:
                    if not isinstance(item, ast.Pass):
                        return True
                    continue
                old_item = before.get(key)
                if key in _HOOKS or (
                    (old_item is None or _structure(item) != _structure(old_item))
                    and _unsafe(item, old_item)
                ):
                    return True
            return False
        return any(_side_effect(arg) for arg in ast.iter_child_nodes(node.args)) or (
            node.returns is not None and _side_effect(node.returns)
        )
    return _side_effect(node)


def _import_hooks(declarations: dict[str, ast.stmt]) -> dict[str, str]:
    hooks = {}
    for name, node in declarations.items():
        if name in _HOOKS:
            hooks[name] = ast.dump(node)
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, _DEFS) and member.name in _HOOKS:
                    hooks[f"{name}.{member.name}"] = ast.dump(member)
    return hooks


def symbol_delta(
    before: bytes, after: bytes, *, package_init: bool = False
) -> SymbolDelta:
    """Keep executable or ambiguous declaration changes module-wide."""
    left, right = (ast.parse(source).body for source in (before, after))
    old_imports, new_imports = (
        [ast.dump(node) for node in body if isinstance(node, _IMPORTS)]
        for body in (left, right)
    )
    out = SymbolDelta(imports=old_imports != new_imports)
    layouts = []
    for body in (left, right):
        declarations = {}
        other = []
        order = []
        for node in body:
            if isinstance(node, _IMPORTS):
                continue
            if key := _key(node):
                if key in declarations:
                    other.append(_structure(node))
                declarations[key] = node
                order.append(key)
            else:
                other.append(_structure(node))
        layouts.append((declarations, other, order))
    (old, old_other, old_order), (new, new_other, new_order) = layouts
    common = old.keys() & new.keys()
    out.broad = (
        old_other != new_other
        or _import_hooks(old) != _import_hooks(new)
        or [key for key in old_order if key in common]
        != [key for key in new_order if key in common]
    )
    for key in old.keys() | new.keys():
        a, b = old.get(key), new.get(key)
        if a is not None and b is not None and _structure(a) == _structure(b):
            continue
        out.symbols.add(key)
        out.broad |= key in _HOOKS or any(
            node is not None and _unsafe(node, peer) for node, peer in ((a, b), (b, a))
        )
    if package_init and (out.imports or out.symbols):
        out.broad = True
    return out
