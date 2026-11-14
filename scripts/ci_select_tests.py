"""Static, fail-open test selection; never import application or test code."""

from __future__ import annotations

import ast
import fnmatch
from functools import cache
from pathlib import Path

from scripts.ci_test_dependencies import ROOT, test_inputs

CI_TESTS = (
    "tests/test_ci_changes.py",
    "tests/test_ci_selection.py",
    "tests/test_github_actions_policy.py",
)
SMOKE_TESTS = ("tests/test_microarray_vendor_formats.py", "tests/test_table_writer.py")
OS_WORKFLOWS = {
    "ubuntu": ".github/workflows/ubuntu_ci.yml",
    "macos": ".github/workflows/macos_ci.yml",
    "windows": ".github/workflows/windows_ci.yml",
    "arm64": ".github/workflows/linux_arm64.yml",
}


def ci_path(path: str) -> bool:
    return path.startswith(".github/") or (
        path.startswith("scripts/ci_") and path.endswith(".py")
    )


class ImportGraph:
    """Module edges include package initializers and literal lazy registries."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: dict[str, Path] = {}
        for base in (root / "src", root):
            directories = (
                ("wgsextract_cli",) if base.name == "src" else ("tests", "scripts")
            )
            for directory in directories:
                for file in (base / directory).rglob("*.py"):
                    parts = file.relative_to(base).with_suffix("").parts
                    name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
                    self.files[name] = file
        self.edges: dict[str, set[str]] = {}
        self.fixture_cache: dict[Path, set[str]] = {}
        self.unsafe: set[str] = set()
        for name, file in self.files.items():
            try:
                tree = ast.parse(file.read_text(encoding="utf-8"))
                self.edges[name] = self.imports(name, tree)
            except (SyntaxError, UnicodeError, OSError):
                self.edges[name] = set()
                self.unsafe.add(name)

    def module(self, name: str) -> set[str]:
        if name.split(".")[0].startswith("test_") and f"tests.{name}" in self.files:
            name = f"tests.{name}"
        parts = name.split(".")
        return {
            ".".join(parts[:length])
            for length in range(1, len(parts) + 1)
            if ".".join(parts[:length]) in self.files
        }

    def imports(self, name: str, tree: ast.Module) -> set[str]:
        file = self.files[name]
        package = name if file.name == "__init__.py" else name.rpartition(".")[0]
        result = self.module(package) - {name}
        nodes = list(ast.walk(tree))
        parents = {
            child: node for node in nodes for child in ast.iter_child_nodes(node)
        }
        dynamic_names = {"__import__"}
        lazy_names = {"command_handler"}
        for node in nodes:
            if isinstance(node, ast.ImportFrom) and node.module == "importlib":
                dynamic_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "import_module"
                )
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "wgsextract_cli.cli._lazy"
            ):
                lazy_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "command_handler"
                )
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    result.update(self.module(alias.name))
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    parts = package.split(".")
                    prefix = ".".join(parts[: len(parts) - node.level + 1])
                    if node.module:
                        prefix += "." + node.module
                result.update(self.module(prefix))
                for alias in node.names:
                    result.update(self.module(f"{prefix}.{alias.name}"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Includes patch targets, subprocess -m and literal import_module.
                if node.value.startswith("wgsextract_cli."):
                    result.update(self.module(node.value))
                if (
                    name.startswith("tests.")
                    and node.value
                    in {
                        "wgsextract",
                        "wgsextract-cli",
                        "wgsextract_cli",
                    }
                    and isinstance(parents.get(node), (ast.List, ast.Tuple))
                ):
                    result.update(self.module("wgsextract_cli.main"))
            elif isinstance(node, ast.Call):
                func = node.func
                dynamic = (isinstance(func, ast.Name) and func.id in dynamic_names) or (
                    isinstance(func, ast.Attribute) and func.attr == "import_module"
                )
                lazy = isinstance(func, ast.Name) and func.id in lazy_names
                if not (dynamic or lazy):
                    continue
                argument = node.args[0] if node.args else None
                if isinstance(argument, ast.Constant) and isinstance(
                    argument.value, str
                ):
                    if argument.value.startswith("."):
                        anchor = package
                        if len(node.args) > 1:
                            supplied = node.args[1]
                            if not isinstance(supplied, ast.Constant) or not isinstance(
                                supplied.value, str
                            ):
                                self.unsafe.add(name)
                                continue
                            anchor = supplied.value
                        dots = len(argument.value) - len(argument.value.lstrip("."))
                        prefix = ".".join(
                            anchor.split(".")[: len(anchor.split(".")) - dots + 1]
                        )
                        result.update(self.module(prefix + "." + argument.value[dots:]))
                    else:
                        result.update(self.module(argument.value))
                elif name == "wgsextract_cli.cli._lazy":
                    # Resolved at command_handler call sites, not on this shared
                    # execution wrapper (which must not depend on every command).
                    if not (
                        isinstance(argument, ast.Attribute)
                        and isinstance(argument.value, ast.Name)
                        and argument.value.id == "self"
                        and argument.attr == "_module"
                    ):
                        self.unsafe.add(name)
                elif name == "wgsextract_cli.commands.registry":
                    targets = self.registry_targets(tree, argument)
                    if targets is None:
                        self.unsafe.add(name)
                    else:
                        result.update(targets)
                else:
                    self.unsafe.add(name)
        return result

    def registry_targets(
        self, tree: ast.Module, argument: ast.AST | None
    ) -> set[str] | None:
        """Resolve #804's finite CommandRegistration comprehension explicitly."""
        if not (
            isinstance(argument, ast.JoinedStr)
            and len(argument.values) == 2
            and isinstance(argument.values[0], ast.Constant)
            and argument.values[0].value == "wgsextract_cli.cli.registration."
            and isinstance(argument.values[1], ast.FormattedValue)
            and isinstance(argument.values[1].value, ast.Attribute)
            and ast.unparse(argument.values[1].value) == "self.module"
        ):
            return None
        for node in ast.walk(tree):
            if not isinstance(node, ast.GeneratorExp):
                continue
            if not (
                isinstance(node.elt, ast.Call)
                and isinstance(node.elt.func, ast.Name)
                and node.elt.func.id == "CommandRegistration"
                and node.elt.args
                and isinstance(node.elt.args[0], ast.Name)
                and len(node.generators) == 1
            ):
                continue
            generator = node.generators[0]
            if (
                not isinstance(generator.target, ast.Name)
                or generator.target.id != node.elt.args[0].id
                or generator.ifs
                or not isinstance(generator.iter, (ast.Tuple, ast.List))
            ):
                return None
            targets = set()
            for item in generator.iter.elts:
                if not isinstance(item, ast.Constant) or not isinstance(
                    item.value, str
                ):
                    return None
                target = f"wgsextract_cli.cli.registration.{item.value}"
                if target not in self.files:
                    return None
                targets.update(self.module(target))
            return targets
        return None

    def reaches(self, name: str) -> set[str]:
        seen: set[str] = set()
        pending = [name]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(self.edges.get(current, set()) - seen)
        return seen

    def fixture_dependencies(self, directory: Path) -> set[str]:
        """Tests implicitly import conftest and its shared helper dependencies."""
        if directory in self.fixture_cache:
            return self.fixture_cache[directory]
        dependencies: set[str] = set()

        for parent in (directory, *directory.parents):
            if not parent.is_relative_to(self.root):
                break
            conftest = parent / "conftest.py"
            if conftest.is_file():
                name = ".".join(conftest.relative_to(self.root).with_suffix("").parts)
                dependencies.update(self.reaches(name))
        self.fixture_cache[directory] = dependencies
        return dependencies


@cache
def import_graph(root: Path = ROOT) -> ImportGraph:
    return ImportGraph(root.resolve())


def select_tests(paths: list[str], *, repository: Path = ROOT) -> list[str] | str:
    """Return sorted existing test files, or 'all' for an uncertain dependency."""
    root = repository.resolve()
    graph = import_graph(root)
    tests = {
        file.relative_to(root).as_posix(): name
        for name, file in graph.files.items()
        if name.startswith("tests.") and file.name.startswith("test_")
    }
    selected: set[str] = set()
    changed_modules: set[str] = set()
    inputs = test_inputs(root)
    by_path = {
        file.relative_to(root).as_posix(): name for name, file in graph.files.items()
    }
    closures = {
        test: graph.reaches(name) | graph.fixture_dependencies((root / test).parent)
        for test, name in tests.items()
    }
    for path in paths:
        if ci_path(path):
            selected.update(test for test in CI_TESTS if test in tests)
            continue
        if path in {"pyproject.toml", "pixi.toml", "pixi.lock"}:
            return "all"
        if path.startswith("src/wgsextract_cli/assets/"):
            # Runtime resource readers are not yet part of the literal-input
            # scanner. A direct test reader must not hide indirect consumers.
            return "all"
        if path.startswith("tests/") and (
            Path(path).name == "conftest.py"
            or "helper" in path
            or path.startswith(("tests/fixtures/", "tests/data/"))
            or path not in tests
        ):
            return "all"
        readers = {
            test
            for pattern, modules in inputs.items()
            if path == pattern or fnmatch.fnmatchcase(path, pattern)
            for test in modules
        }
        selected.update(readers & tests.keys())
        reader_modules = {by_path[reader] for reader in readers if reader in by_path}
        selected.update(
            test for test, reached in closures.items() if reached & reader_modules
        )
        if path in tests:
            selected.add(path)
        if path in by_path:
            changed_modules.add(by_path[path])
        elif path.startswith("src/wgsextract_cli/") and path.endswith(".py"):
            return "all"
        elif not readers and not (
            path.endswith(".md")
            or path.startswith(("gui/", "native/", "site/", "site_src/"))
        ):
            return "all"
    if changed_modules:
        for test, reached in closures.items():
            if reached & graph.unsafe:
                if (
                    "wgsextract_cli.main" in reached
                    or "wgsextract_cli.cli.parser" in reached
                ):
                    return "all"
                selected.add(test)
            if reached & changed_modules:
                selected.add(test)
    return sorted(selected)


def os_selection(
    paths: list[str], selection: list[str] | str, *, cli: bool
) -> dict[str, list[str] | str]:
    return {
        os: selection if cli else list(SMOKE_TESTS) if workflow in paths else []
        for os, workflow in OS_WORKFLOWS.items()
    }
