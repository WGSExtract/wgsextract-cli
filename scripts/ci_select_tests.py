"""Static, fail-open test selection; never import application or test code."""

from __future__ import annotations

import ast
import fnmatch
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib.util import resolve_name
from pathlib import Path

from scripts.ci_test_dependencies import (
    GENERATED_DOCS,
    ROOT,
    docs_test_targets,
    test_inputs,
)

CI_TESTS = (
    "tests/test_ci_changes.py",
    "tests/test_ci_landing.py",
    "tests/test_ci_required.py",
    "tests/test_ci_selection.py",
    "tests/test_github_actions_policy.py",
    "tests/test_ci_installlab_apt.py",
    "tests/test_ci_codeql_scope_policy.py",
    "tests/test_ci_main_dedupe_policy.py",
    "tests/test_workflow_readiness.py",
    "tests/test_workflow_paths.py",
    "tests/test_python_size.py",
    "tests/test_test_smells.py",
    "tests/test_mypy_policy.py",
    "tests/test_ci_certify.py",
    "tests/test_ci_certify_remote.py",
    "tests/test_ci_reference_store.py",
    "tests/test_ci_scheduling.py",
    "tests/test_ci_moves.py",
    "tests/test_ci_impact_scopes.py",
    "tests/test_ci_impact_shadow.py",
    "tests/test_ci_impact_symbols.py",
    "tests/test_ci_impact_wide.py",
    "tests/test_ci_impact_gate.py",
    "tests/test_ci_impact_fetch.py",
    "tests/test_ci_auto_merge_watch.py",
    "tests/test_ci_release_latest.py",
    "tests/test_ci_release_gate.py",
    "tests/test_ci_capacity.py",
    "tests/test_ci_cache_prune.py",
    "tests/test_ci_efficiency_report.py",
    "tests/test_ci_test_usefulness.py",
)
SMOKE_TESTS = ("tests/test_microarray_vendor_formats.py", "tests/test_table_writer.py")
# These helpers replaced imports from test_server.py. Their consumers still
# import them explicitly, so the import graph can select the affected tests.
SERVER_TEST_HELPERS = frozenset(
    {
        "tests/server_data.py",
        "tests/server_helpers.py",
        "tests/server_path_cases.py",
    }
)
REMOTE_CERTIFICATION_TEST = "tests/test_ci_certify_remote.py"
REMOTE_CERTIFICATION_INPUTS = {
    REMOTE_CERTIFICATION_TEST,
    "scripts/ci_certify.py",
    "scripts/ci_certify_hosts.py",
    "scripts/ci_certify_remote.py",
    "scripts/ci_certify_remote_worker.py",
    "scripts/ci_certify_ssh.py",
}
OS_WORKFLOWS = {
    "ubuntu": ".github/workflows/ubuntu_ci.yml",
    "macos": ".github/workflows/macos_ci.yml",
    "windows": ".github/workflows/windows_ci.yml",
    "arm64": ".github/workflows/linux_arm64.yml",
}

# Build policy names from the package prefix so the selector's own literal
# scan doesn't mistake these comparisons for application dependencies.
APP_PACKAGE = "wgsextract_cli"
APP_PREFIX = APP_PACKAGE + "."
CLI_MAIN = APP_PREFIX + "main"
CLI_PARSER = APP_PREFIX + "cli.parser"
LAZY_LOADER = APP_PREFIX + "cli._lazy"
COMMAND_REGISTRY = APP_PREFIX + "commands.registry"
REGISTRATION_PREFIX = APP_PREFIX + "cli.registration."


def ci_path(path: str) -> bool:
    return not path.endswith(".md") and (
        path in CI_TESTS
        or path
        in {
            "scripts/check_python_size.py",
            "scripts/python_size_baseline.json",
            "scripts/test_smells.py",
        }
        or path.startswith(".github/")
        or (path.startswith("scripts/ci_") and path.endswith(".py"))
    )


def _call_argument(call: ast.Call, index: int, keyword: str) -> ast.AST | None:
    if index < len(call.args):
        return call.args[index]
    return next((arg.value for arg in call.keywords if arg.arg == keyword), None)


class ImportGraph:
    """Module edges include package initializers and literal lazy registries."""

    def __init__(
        self,
        root: Path,
        *,
        files: dict[str, Path] | None = None,
        parse: Callable[[Path], ast.Module] | None = None,
    ) -> None:
        self.root = root
        self.files = self.checkout_files(root) if files is None else files
        self.edges: dict[str, set[str]] = {}
        self.import_edges: dict[str, set[str]] = {}
        self.fixture_cache: dict[tuple[Path, bool], set[str]] = {}
        self.reach_cache: dict[tuple[str, bool], frozenset[str]] = {}
        self.unsafe: set[str] = set()
        for name, file in self.files.items():
            try:
                tree = (
                    parse(file)
                    if parse
                    else ast.parse(file.read_text(encoding="utf-8"))
                )
                imports, references = self.imports(name, tree)
                self.import_edges[name] = imports
                self.edges[name] = imports | references
            except (SyntaxError, ValueError, OSError):
                self.edges[name] = set()
                self.unsafe.add(name)

    @staticmethod
    def checkout_files(root: Path) -> dict[str, Path]:
        files = {}
        if (conftest := root / "conftest.py").is_file():
            files["conftest"] = conftest
        for base in (root / "src", root):
            directories = (
                ("wgsextract_cli",) if base.name == "src" else ("tests", "scripts")
            )
            for directory in directories:
                for file in (base / directory).rglob("*.py"):
                    parts = file.relative_to(base).with_suffix("").parts
                    name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
                    files[name] = file
        return files

    def module(self, name: str) -> set[str]:
        if name.split(".")[0].startswith("test_") and f"tests.{name}" in self.files:
            name = f"tests.{name}"
        parts = name.split(".")
        return {
            ".".join(parts[:length])
            for length in range(1, len(parts) + 1)
            if ".".join(parts[:length]) in self.files
        }

    def imports(self, name: str, tree: ast.Module) -> tuple[set[str], set[str]]:
        file = self.files[name]
        package = name if file.name == "__init__.py" else name.rpartition(".")[0]
        result = self.module(package) - {name}
        references: set[str] = set()
        nodes = list(ast.walk(tree))
        parents = {
            child: node for node in nodes for child in ast.iter_child_nodes(node)
        }
        builtin_names = {"__import__"}
        dynamic_names: set[str] = set()
        lazy_names = {"command_handler"}
        for node in nodes:
            if isinstance(node, ast.ImportFrom) and node.module == "importlib":
                dynamic_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "import_module"
                )
            if isinstance(node, ast.ImportFrom) and node.module == "builtins":
                builtin_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "__import__"
                )
            if isinstance(node, ast.ImportFrom) and node.module == LAZY_LOADER:
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
                # Patch targets and subprocess -m are useful, but not proof of an import.
                if node.value.startswith(APP_PREFIX):
                    references.update(self.module(node.value))
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
                    references.update(self.module(CLI_MAIN))
            elif isinstance(node, ast.Call):
                func = node.func
                builtin = (isinstance(func, ast.Name) and func.id in builtin_names) or (
                    isinstance(func, ast.Attribute) and func.attr == "__import__"
                )
                dynamic = (
                    builtin
                    or (isinstance(func, ast.Name) and func.id in dynamic_names)
                    or (
                        isinstance(func, ast.Attribute) and func.attr == "import_module"
                    )
                )
                lazy = isinstance(func, ast.Name) and func.id in lazy_names
                if not (dynamic or lazy):
                    continue
                if builtin and (level := _call_argument(node, 4, "level")) is not None:
                    if not (isinstance(level, ast.Constant) and level.value == 0):
                        self.unsafe.add(name)
                        continue
                argument = _call_argument(node, 0, "module" if lazy else "name")
                if isinstance(argument, ast.Constant) and isinstance(
                    argument.value, str
                ):
                    target = argument.value
                    if argument.value.startswith("."):
                        supplied = _call_argument(node, 1, "package")
                        if isinstance(supplied, ast.Constant) and isinstance(
                            supplied.value, str
                        ):
                            anchor = supplied.value
                        elif (
                            isinstance(supplied, ast.Name)
                            and supplied.id == "__package__"
                        ):
                            anchor = package
                        else:
                            self.unsafe.add(name)
                            continue
                        try:
                            target = resolve_name(target, anchor)
                        except ImportError:
                            self.unsafe.add(name)
                            continue
                    result.update(self.module(target))
                    if (
                        builtin
                        and (names := _call_argument(node, 3, "fromlist")) is not None
                    ):
                        if isinstance(names, ast.Constant) and names.value is None:
                            continue
                        if not isinstance(names, (ast.List, ast.Tuple)):
                            self.unsafe.add(name)
                            continue
                        for item in names.elts:
                            if (
                                isinstance(item, ast.Constant)
                                and isinstance(item.value, str)
                                and item.value != "*"
                            ):
                                result.update(self.module(f"{target}.{item.value}"))
                            else:
                                self.unsafe.add(name)
                elif name == LAZY_LOADER:
                    # Resolved at command_handler call sites, not on this shared
                    # execution wrapper (which must not depend on every command).
                    if not (
                        isinstance(argument, ast.Attribute)
                        and isinstance(argument.value, ast.Name)
                        and argument.value.id == "self"
                        and argument.attr == "_module"
                    ):
                        self.unsafe.add(name)
                elif name == COMMAND_REGISTRY:
                    targets = self.registry_targets(tree, argument)
                    if targets is None:
                        self.unsafe.add(name)
                    else:
                        result.update(targets)
                else:
                    self.unsafe.add(name)
        return result, references

    def registry_targets(
        self, tree: ast.Module, argument: ast.AST | None
    ) -> set[str] | None:
        """Resolve #804's finite CommandRegistration comprehension explicitly."""
        if not (
            isinstance(argument, ast.JoinedStr)
            and len(argument.values) == 2
            and isinstance(argument.values[0], ast.Constant)
            and argument.values[0].value == REGISTRATION_PREFIX
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
                target = REGISTRATION_PREFIX + item.value
                if target not in self.files:
                    return None
                targets.update(self.module(target))
            return targets
        return None

    def reaches(self, name: str, *, imports_only: bool = False) -> frozenset[str]:
        key = (name, imports_only)
        if key in self.reach_cache:
            return self.reach_cache[key]
        edges = self.import_edges if imports_only else self.edges
        seen: set[str] = set()
        pending = [name]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(edges.get(current, set()) - seen)
        self.reach_cache[key] = frozenset(seen)
        return self.reach_cache[key]

    def fixture_dependencies(
        self, directory: Path, *, imports_only: bool = False
    ) -> set[str]:
        """Tests implicitly import conftest and its shared helper dependencies."""
        key = (directory, imports_only)
        if key in self.fixture_cache:
            return self.fixture_cache[key]
        dependencies: set[str] = set()

        for parent in (directory, *directory.parents):
            if not parent.is_relative_to(self.root):
                break
            conftest = parent / "conftest.py"
            name = ".".join(conftest.relative_to(self.root).with_suffix("").parts)
            if name in self.files:
                dependencies.update(self.reaches(name, imports_only=imports_only))
        self.fixture_cache[key] = dependencies
        return dependencies


@cache
def import_graph(root: Path = ROOT) -> ImportGraph:
    return ImportGraph(root.resolve())


@dataclass(frozen=True)
class SelectionContext:
    tests: dict[str, str]
    by_path: dict[str, str]
    direct_closures: dict[str, frozenset[str]]
    closures: dict[str, frozenset[str]]


@cache
def selection_context(root: Path) -> SelectionContext:
    """Per-checkout graph views, shared by every selection query."""
    graph = import_graph(root)
    tests = {
        file.relative_to(root).as_posix(): name
        for name, file in graph.files.items()
        if name.startswith("tests.") and file.name.startswith("test_")
    }
    direct_closures = {test: graph.reaches(name) for test, name in tests.items()}
    return SelectionContext(
        tests=tests,
        by_path={
            file.relative_to(root).as_posix(): name
            for name, file in graph.files.items()
        },
        direct_closures=direct_closures,
        closures={
            test: direct_closures[test]
            | graph.fixture_dependencies((root / test).parent)
            for test in tests
        },
    )


def select_tests(
    paths: list[str], *, repository: Path = ROOT, known_only: bool = False
) -> list[str] | str:
    """Return affected files, or 'all' for uncertainty unless collecting known users."""
    root = repository.resolve()
    graph = import_graph(root)
    context = selection_context(root)
    tests = context.tests
    selected = {
        target.split("::")[0]
        for target in docs_test_targets(paths, repository=root)
        if target.split("::")[0] in tests
    }
    changed_modules: set[str] = set()
    ci_modules: set[str] = set()
    inputs = test_inputs(root)
    by_path = context.by_path
    direct_closures = context.direct_closures
    closures = context.closures
    for path in paths:
        if path.endswith(".md") or path in GENERATED_DOCS:
            continue
        if ci_path(path):
            selected.update(test for test in CI_TESTS if test in tests)
        if not known_only and (
            path in {"pyproject.toml", "pixi.toml", "pixi.lock"}
            or (path.startswith("src/wgsextract_cli/") and not path.endswith(".py"))
            or (
                path.startswith("tests/")
                and path not in SERVER_TEST_HELPERS
                and (
                    Path(path).name == "conftest.py"
                    or "helper" in path
                    or path.startswith(("tests/fixtures/", "tests/data/"))
                    or path not in tests
                )
            )
        ):
            return "all"
        readers = {
            test
            for pattern, modules in inputs.items()
            if path == pattern or fnmatch.fnmatchcase(path, pattern)
            for test in modules
            if ci_path(path) or test not in CI_TESTS
        }
        selected.update(readers & tests.keys())
        reader_modules = {by_path[reader] for reader in readers if reader in by_path}
        reader_closures = direct_closures if ci_path(path) else closures
        selected.update(
            test
            for test, reached in reader_closures.items()
            if reached & reader_modules
        )
        if path in tests:
            selected.add(path)
        if path in by_path:
            (ci_modules if ci_path(path) else changed_modules).add(by_path[path])
        elif not known_only:
            if path.startswith("src/wgsextract_cli/") or (
                not readers
                and not ci_path(path)
                and (
                    path.endswith(".py")
                    or not path.startswith(("gui/", "native/", "site/", "site_src/"))
                )
            ):
                return "all"
    if changed_modules:
        for test, reached in closures.items():
            if not known_only and reached & graph.unsafe:
                if CLI_MAIN in reached or CLI_PARSER in reached:
                    return "all"
                selected.add(test)
            if reached & changed_modules:
                selected.add(test)
    selected.update(
        test for test, reached in direct_closures.items() if reached & ci_modules
    )
    return sorted(selected)


def ready_test_selection(
    paths: list[str], selection: list[str] | str, *, repository: Path = ROOT
) -> list[str]:
    """Omit measured slow modules only from broad, unproven fallback coverage."""
    if isinstance(selection, list):
        return selection
    from scripts.ci_test_tiers import fast_test

    known = select_tests(paths, repository=repository, known_only=True)
    assert isinstance(known, list)
    return sorted(
        set(known)
        | {test for test in selection_context(repository).tests if fast_test(test)}
    )


def os_selection(
    paths: list[str], selection: list[str] | str, *, cli: bool
) -> dict[str, list[str] | str]:
    if cli:
        return dict.fromkeys(OS_WORKFLOWS, selection)
    remote = bool(REMOTE_CERTIFICATION_INPUTS.intersection(paths))
    result: dict[str, list[str] | str] = {}
    for os, workflow in OS_WORKFLOWS.items():
        smoke = workflow in paths or ".github/workflows/os_tests.yml" in paths
        # The supervisor exercises native Windows jobs and POSIX process groups.
        result[os] = [
            *(SMOKE_TESTS if smoke else ()),
            *([REMOTE_CERTIFICATION_TEST] if remote else []),
        ]
    return result


def ci_test_targets(paths: list[str], *, repository: Path = ROOT) -> list[str]:
    """Baseline policy checks plus actual CI script/action dependency readers."""
    selected = select_tests(
        [path for path in paths if ci_path(path)], repository=repository
    )
    return list(CI_TESTS) if selected == "all" else list(selected)
