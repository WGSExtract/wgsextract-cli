"""Resolve static callers on both sides of the tested PR tree."""

from __future__ import annotations

import ast
import subprocess
from collections import defaultdict
from functools import cache
from pathlib import Path


def _module(path: str) -> str:
    name = path.removeprefix("src/").removesuffix(".py")
    return name.removesuffix("/__init__").replace("/", ".")


@cache
def tracked_python(root: Path, sha: str) -> dict[str, str]:
    entries = subprocess.check_output(
        ["git", "ls-tree", "-rz", sha, "--", "src", "tests", "scripts"], cwd=root
    )
    result = {}
    for entry in entries.split(b"\0"):
        if entry:
            meta, name = entry.split(b"\t", 1)
            path = name.decode()
            if meta.split()[1] == b"blob" and path.endswith(".py"):
                result[path] = meta.split()[2].decode()
    return result


def _reads(node: ast.AST) -> set[str]:
    return {
        child.id if isinstance(child, ast.Name) else child.attr
        for child in ast.walk(node)
        if isinstance(child, (ast.Name, ast.Attribute))
        and isinstance(child.ctx, ast.Load)
    }


@cache
def _parse(root: Path, blob: str) -> ast.Module:
    return ast.parse(
        subprocess.check_output(["git", "cat-file", "blob", blob], cwd=root)
    )


class StaticUsers:
    def __init__(self, root: Path, sha: str) -> None:
        self.root = root
        self.files = tracked_python(root, sha)
        self.importers: dict[str, set[str]] = defaultdict(set)
        self.reads: dict[str, set[str]] = {}
        self.calls: dict[str, dict[str, set[str]]] = {}
        self.unparsed: set[str] = set()
        for path, blob in self.files.items():
            if not path.startswith("scripts/"):
                self._add(path, blob)

    def _add(self, path: str, blob: str) -> None:
        try:
            tree = _parse(self.root, blob)
        except (SyntaxError, ValueError):
            self.unparsed.add(path)
            return
        name = _module(path)
        package = name if path.endswith("/__init__.py") else name.rpartition(".")[0]
        self.reads[path] = _reads(tree)
        self.calls[path] = defaultdict(set)
        for node in ast.walk(tree):
            match node:
                case ast.Import(names=aliases):
                    imports = {alias.name for alias in aliases}
                case ast.ImportFrom(module=stem, level=level, names=aliases):
                    prefix = stem or ""
                    if level:
                        parts = package.split(".")
                        prefix = ".".join(
                            parts[: len(parts) + 1 - level]
                            + ([prefix] if prefix else [])
                        )
                    imports = {prefix, *(f"{prefix}.{alias.name}" for alias in aliases)}
                    self.reads[path].update(alias.name for alias in aliases)
                    # Carry aliases through dispatch wrappers and re-exports.
                    for alias in aliases:
                        if alias.asname:
                            self.calls[path][alias.asname].add(alias.name)
                case (
                    ast.FunctionDef(name=fn, body=body)
                    | ast.AsyncFunctionDef(name=fn, body=body)
                ):
                    self.calls[path][fn].update(*(map(_reads, body)))
                    continue
                case _:
                    continue
            for imported in imports:
                self.importers[imported].add(path)

    def symbol_users(
        self,
        path: str,
        names: set[str],
        tests: set[str],
        observed: dict[str, dict[str, list[str]]],
    ) -> set[str]:
        selected = set()
        frontier = {path: names}
        for _ in range(2):
            next_round: dict[str, set[str]] = defaultdict(set)
            for dependency, symbols in frontier.items():
                for importer in self.importers.get(_module(dependency), set()) | {
                    dependency
                }:
                    if self.reads.get(importer, set()) & symbols:
                        if importer in tests:
                            selected.add(importer)
                        if next_names := {
                            fn
                            for fn, reads in self.calls[importer].items()
                            if reads & symbols
                        }:
                            next_round[importer].update(next_names)
                            for fn, users in observed.get(importer, {}).items():
                                if fn.rsplit(".", 1)[-1] in next_names:
                                    selected.update(users)
            frontier = next_round
        return selected

    def historical_importers(self, path: str, tests: set[str]) -> set[str]:
        result = set()
        pending = [path]
        visited = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            if current in tests:
                result.add(current)
                continue
            pending.extend(self.importers.get(_module(current), ()))
        return result
