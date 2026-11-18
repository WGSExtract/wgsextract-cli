"""Prove Python-only moves/import rewrites; uncertain diffs keep normal coverage."""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from scripts.ci_select_tests import import_graph, selection_context
from scripts.ci_test_dependencies import ROOT, test_inputs

WHOLE_SOURCE_GLOBS = frozenset(
    {"src/**", "src/**/*.py", "src/wgsextract_cli/**", "src/wgsextract_cli/**/*.py"}
)


class WithoutImports(ast.NodeTransformer):
    def visit_Import(self, node: ast.Import) -> None:
        return None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        return None


def definitions(source: bytes) -> str:
    return ast.dump(WithoutImports().visit(ast.parse(source)), include_attributes=False)


@dataclass(frozen=True)
class MoveSelection:
    tests: list[str]
    modules: list[str]


def references_removed(
    files: dict[str, Path], renames: dict[str, str], root: Path
) -> bool:
    old_modules = set()
    for path in renames:
        parts = Path(path).relative_to("src").with_suffix("").parts
        old_modules.add(".".join(parts[:-1] if parts[-1] == "__init__" else parts))

    def missing(reference: str) -> bool:
        return any(
            reference in {old, old.rsplit(".", 1)[-1]}
            or reference.startswith(old + ".")
            for old in old_modules
        )

    # A byte-identical rename stays in these scanners' source universe.
    # Literal paths and narrower directory readers retain the fallback.
    if any(
        fnmatchcase(old, pattern)
        and not (pattern in WHOLE_SOURCE_GLOBS and fnmatchcase(new, pattern))
        for old, new in renames.items()
        for pattern in test_inputs(root)
    ):
        return True
    for name, file in files.items():
        tree = ast.parse(file.read_text(encoding="utf-8"))
        package = name if file.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(missing(alias.name) for alias in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    package_parts = package.split(".")
                    prefix = ".".join(
                        package_parts[: len(package_parts) - node.level + 1]
                    )
                    if node.module:
                        prefix += "." + node.module
                if missing(prefix) or any(
                    missing(f"{prefix}.{alias.name}") for alias in node.names
                ):
                    return True
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if any(old in node.value for old in old_modules) or any(
                    Path(path).name in node.value for path in renames
                ):
                    return True
    return False


def select_moves(
    base: str, head: str, *, repository: Path = ROOT
) -> MoveSelection | None:
    def git(*args: str) -> bytes:
        return subprocess.check_output(
            ["git", *args], cwd=repository, stderr=subprocess.PIPE
        )

    try:
        # PR checkout is a synthetic merge commit. Its analyzed tree must still
        # match the proved head; newer base changes or dirty inputs fail open.
        git("diff", "--quiet", head, "--", "src", "tests", "scripts")
        before = git("merge-base", base, head).decode().strip()
        fields = (
            git(
                "diff",
                "--name-status",
                "--find-renames=100%",
                "--no-ext-diff",
                "--no-textconv",
                "-z",
                f"{before}...{head}",
                "--",
            )
            .decode()
            .rstrip("\0")
            .split("\0")
        )
        if not fields or fields == [""]:
            return None
        changed: set[str] = set()
        renames: dict[str, str] = {}
        index = 0
        while index < len(fields):
            status, old = fields[index : index + 2]
            index += 2
            new = old
            if status == "R100":
                new = fields[index]
                index += 1
                renames[old] = new
            elif status != "M":
                return None
            for path in (old, new):
                if (
                    not path.startswith("src/wgsextract_cli/")
                    or not path.endswith(".py")
                    or Path(path).name == "__main__.py"
                ):
                    return None
            if definitions(git("show", f"{before}:{old}")) != definitions(
                git("show", f"{head}:{new}")
            ):
                return None
            # Imports rewritten in other modules also need their consumers tested.
            changed.update((old, new))
        if not renames:
            return None
        root = repository.resolve()
        context = selection_context(root)
        graph = import_graph(root)
        if references_removed(graph.files, renames, root):
            return None
        modules = {context.by_path[path] for path in changed if path in context.by_path}
        if not modules or modules & graph.unsafe:
            return None
        tests = {
            test for test, closure in context.closures.items() if closure & modules
        }
        # Import-only edits can still affect whole-tree static checks.
        readers = {
            reader
            for pattern, readers in test_inputs(root).items()
            if pattern in WHOLE_SOURCE_GLOBS
            and any(fnmatchcase(path, pattern) for path in changed)
            for reader in readers
        }
        reader_modules = {
            context.by_path[path] for path in readers if path in context.by_path
        }
        tests.update(readers & context.tests.keys())
        tests.update(
            test
            for test, closure in context.closures.items()
            if closure & reader_modules
        )
        for test, closure in context.closures.items():
            if closure & graph.unsafe:
                if {"wgsextract_cli.main", "wgsextract_cli.cli.parser"} & closure:
                    return None
                tests.add(test)
        return MoveSelection(sorted(tests), sorted(modules))
    except (OSError, ValueError, SyntaxError, IndexError, subprocess.SubprocessError):
        return None
