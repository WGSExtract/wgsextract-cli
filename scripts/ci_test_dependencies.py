"""Conservative repository inputs referenced by the root pytest suite.

Collect imports, repository-relative literals, composed pathlib paths and globs
without importing or executing tests. Ambiguous fixture literals can select
extra tests; they must never suppress an actual repository dependency.
"""

from __future__ import annotations

import ast
import os
from collections import defaultdict
from functools import cache
from pathlib import Path
from typing import TypeAlias

ROOT = Path(__file__).resolve().parents[1]
Value: TypeAlias = Path | str | None
IGNORED = {".git", ".pixi", "out", "tmp", "scratch", "__pycache__", "node_modules"}
DOCS_TEST_TARGETS = (
    "tests/test_api.py::test_api_docs_are_up_to_date",
    "tests/test_api_cli_parity.py",
    "tests/test_cli_help.py",
    "tests/test_bump_version.py",
    "tests/test_build_mockup_previews.py",
    "tests/test_ci_changes.py",
    "tests/test_github_actions_policy.py",
)
DOCS_TEST_MODULES = frozenset(
    target for target in DOCS_TEST_TARGETS if "::" not in target
)


class TestInputs:
    def __init__(self, root: Path, test: Path, tree: ast.Module) -> None:
        self.root = root
        self.test = test
        self.bindings = {
            target.id: node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self.bindings.update(
            {
                node.target.id: node.value
                for node in tree.body
                if isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            }
        )
        self.path_names = {"Path", "PosixPath", "PurePath"} | {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "pathlib"
            for alias in node.names
            if alias.name in {"Path", "PosixPath", "PurePath"}
        }

    def value(self, node: ast.AST, seen: frozenset[str] = frozenset()) -> Value:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                part.value
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
                else "*"
                for part in node.values
            )
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return str(self.test)
            if node.id in self.bindings and node.id not in seen:
                return self.value(self.bindings[node.id], seen | {node.id})
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = self.value(node.left, seen), self.value(node.right, seen)
            if isinstance(left, Path) and isinstance(right, (str, Path)):
                return left / right
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            base = self.value(node.value, seen)
            if isinstance(base, Path):
                return base.parent
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
        ):
            base = self.value(node.value.value, seen)
            if isinstance(base, Path) and node.slice.value < len(base.parents):
                return base.parents[node.slice.value]
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in self.path_names
                and len(node.args) == 1
            ):
                value = self.value(node.args[0], seen)
                if isinstance(value, (str, Path)):
                    path = Path(value)
                    if path.is_absolute():
                        return path
                    # Tests change cwd into temporary directories. Do not
                    # reinterpret Path(".")/temporary globs as repository I/O.
                    first = path.parts[0] if path.parts else ""
                    if (
                        first not in {"", ".", ".."}
                        and not any(char in str(path) for char in "*?[")
                        and (path.suffix or (self.root / first).is_dir())
                    ):
                        return self.root / path
            if isinstance(node.func, ast.Attribute):
                base = self.value(node.func.value, seen)
                if isinstance(base, Path):
                    if node.func.attr == "resolve":
                        return base
                    if node.func.attr in {"glob", "rglob", "joinpath"}:
                        parts = [self.value(arg, seen) for arg in node.args]
                        if all(isinstance(part, str) for part in parts):
                            result = base
                            if node.func.attr == "rglob":
                                result /= "**"
                            for part in parts:
                                assert isinstance(part, str)
                                result /= part
                            return result
                        if node.func.attr in {"glob", "rglob"}:
                            return base / "**"
        return None


@cache
def test_inputs(root: Path = ROOT) -> dict[str, frozenset[str]]:
    """Map repository paths/globs to the test modules that reference them."""
    root = root.resolve()
    references: defaultdict[str, set[str]] = defaultdict(set)

    def record(path: Path, reader: str, *, directory: bool = True) -> None:
        path = Path(os.path.normpath(path))
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            return
        if relative == "." or relative.split("/")[0] in IGNORED:
            return
        references[relative].add(reader)
        if directory and (path.is_dir() or not path.suffix):
            references[f"{relative}/**"].add(reader)

    for test in sorted((root / "tests").rglob("*.py")):
        tree = ast.parse(test.read_text(encoding="utf-8"), filename=str(test))
        reader = test.relative_to(root).as_posix()
        resolver = TestInputs(root, test, tree)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            literal: str | None = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literal = node.value
            elif isinstance(node, ast.JoinedStr):
                literal = "".join(
                    part.value
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                    else "*"
                    for part in node.values
                )
            if literal and "/" in literal and "\n" not in literal:
                first = literal.removeprefix("./").split("/")[0]
                if first not in IGNORED | {"", ".", ".."} and (root / first).is_dir():
                    record(root / literal.removeprefix("./"), reader)

            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                parent = parents.get(node)
                if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):
                    continue
                value = resolver.value(node)
                # Directory anchors are not reads; calls/globs below capture
                # directories passed to readers, including copytree helpers.
                if isinstance(value, Path) and not value.is_dir():
                    record(value, reader)
            elif isinstance(node, ast.Call):
                value = resolver.value(node)
                if isinstance(value, Path) and not value.is_dir():
                    record(value, reader)
                for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                    value = resolver.value(arg)
                    if isinstance(value, Path):
                        record(value, reader)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [
                        node.module or "",
                        *(f"{node.module}.{alias.name}" for alias in node.names),
                    ]
                )
                for module in modules:
                    for base in (root, root / "src", test.parent):
                        candidate = base / module.replace(".", "/")
                        for path in (
                            candidate.with_suffix(".py"),
                            candidate / "__init__.py",
                        ):
                            if path.is_file():
                                record(path, reader)
    return {path: frozenset(readers) for path, readers in references.items()}
