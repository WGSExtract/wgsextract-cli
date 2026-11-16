"""Prove Python-only moves/import rewrites; uncertain diffs keep normal coverage."""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scripts.ci_select_tests import import_graph, selection_context
from scripts.ci_test_dependencies import ROOT


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
        renamed = False
        index = 0
        while index < len(fields):
            status, old = fields[index : index + 2]
            index += 2
            new = old
            if status == "R100":
                new = fields[index]
                index += 1
                renamed = True
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
        if not renamed:
            return None
        root = repository.resolve()
        context = selection_context(root)
        graph = import_graph(root)
        modules = {context.by_path[path] for path in changed if path in context.by_path}
        if not modules or modules & graph.unsafe:
            return None
        tests = sorted(
            test
            for test, closure in context.direct_closures.items()
            if closure & modules
        )
        return MoveSelection(tests, sorted(modules))
    except (OSError, ValueError, SyntaxError, IndexError, subprocess.SubprocessError):
        return None
