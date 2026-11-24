"""Explicit slow-module partition; unclassified/new tests stay in the core tier."""

import ast
import hashlib
import re
from functools import cache
from pathlib import Path

# These suites exercise real workflows, resumability and multiple tool stages.
# Keep whole modules together: their module fixtures are intentionally shared.
INTEGRATION_MODULES = frozenset(
    {
        "test_realign.py",
        "test_realign_resources.py",
        "test_realign_resume.py",
        "test_realign_streaming.py",
        "test_analyze_workflows.py",
        "test_benchmark.py",
        "test_benchmark_modules.py",
        "test_pipeline_interop.py",
        "test_contig_calling.py",
        "test_microarray_reuse.py",
        "test_native_aligners.py",
        "test_native_aligners_gpu_memory.py",
        "test_native_aligners_linux.py",
        "test_native_aligners_mem3.py",
        "test_native_aligners_minimap2.py",
        "test_deepvariant.py",
        "test_deepvariant_execution.py",
        "test_deepvariant_gpu.py",
        "test_deepvariant_gvcf.py",
        "test_deepvariant_native.py",
        "test_deepvariant_staging.py",
    }
)

SENSITIVE = re.compile(
    r"subprocess|platform|sys\.|os\.|shutil|signal|chmod|symlink|newline|"
    r"line.?ending|CRLF|\\r|samtools|bcftools|bwa|tool|shell|"
    r"Popen|check_output|check_call|exec|eval|compile|importlib|__import__|"
    r"getfixturevalue|pytest_plugins|usefixtures|runner|CliRunner|"
    r"\bfixture\b|pytest\.main|runpytest|getattr|globals\(|locals\(|vars\(",
    re.IGNORECASE,
)
# Shared autouse isolation is infrastructure, not a shell-out fixture. Unknown
# requested fixtures are deliberately rejected rather than guessed from names.
PURE_FIXTURES = {
    "monkeypatch",
    "capsys",
    "caplog",
    "recwarn",
}
PURE_IMPORTS = {
    "__future__",
    "collections",
    "csv",
    "dataclasses",
    "enum",
    "functools",
    "gzip",
    "hashlib",
    "io",
    "itertools",
    "json",
    "logging",
    "math",
    "pytest",
    "re",
    "statistics",
    "string",
    "struct",
    "tomllib",
    "tomli",
    "typing",
}
HARNESS_FIXTURES = {
    "_isolate_session_config",
    "_isolate_user_config",
    "_no_segmented_size_probe",
    "_fresh_tool_caches",
    "_restore_signal_handlers",
}


def pure_fixtures(path: Path) -> bool:
    root = Path(__file__).resolve().parents[1]
    for directory in (path.parent, *path.parent.parents):
        conftest = directory / "conftest.py"
        if not conftest.is_file():
            continue
        tree = ast.parse(conftest.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "pytest_plugins":
                return False
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "pytest_plugins"
                for target in node.targets
            ):
                return False
            if isinstance(node, ast.Import) and any(
                alias.name == "subprocess" for alias in node.names
            ):
                return False
            if isinstance(node, ast.ImportFrom) and (
                node.module == "subprocess"
                or (
                    node.module == "os"
                    and any(
                        alias.name in {"system", "popen", "spawn"}
                        for alias in node.names
                    )
                )
            ):
                return False
            if isinstance(node, ast.Call) and (
                ast.unparse(node.func).split(".")[-1]
                in {
                    "run_command",
                    "Popen",
                    "check_call",
                    "check_output",
                    "system",
                    "exec",
                    "eval",
                }
            ):
                return False
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in PURE_FIXTURES:
                return False
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                for keyword in decorator.keywords:
                    if keyword.arg == "autouse" and not (
                        isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is False
                    ):
                        if (
                            conftest != root / "tests/conftest.py"
                            or node.name not in HARNESS_FIXTURES
                        ):
                            return False
    return True


def pure_imports(path: Path, tree: ast.Module) -> bool:
    from scripts.ci_select_tests import import_graph

    root = Path(__file__).resolve().parents[1]
    graph = import_graph(root)
    module = (
        ".".join(path.relative_to(root).with_suffix("").parts)
        if path.is_relative_to(root)
        else ""
    )
    dependencies = set(graph.reaches(module)) - {module}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in imports:
                if name.startswith("wgsextract_cli"):
                    if name not in graph.files:
                        return False
                    dependencies.update(graph.reaches(name))
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    for name in graph.module(f"{node.module}.{alias.name}"):
                        dependencies.update(graph.reaches(name))
    trees = [tree]
    for name in dependencies:
        if name in graph.unsafe:
            return False
        dependency = ast.parse(graph.files[name].read_text(encoding="utf-8"))
        if SENSITIVE.search(ast.unparse(dependency)):
            return False
        trees.append(dependency)
    for parsed in trees:
        for node in ast.walk(parsed):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in modules:
                    if name.startswith("wgsextract_cli") and name in graph.files:
                        continue
                    if isinstance(node, ast.ImportFrom) and node.level and module:
                        continue
                    if name.split(".")[0] not in PURE_IMPORTS:
                        return False
    return True


def generic_test(path: Path) -> bool:
    """Prove a test file free of platform signals and unreviewed fixtures."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, UnicodeError, SyntaxError):
        return False
    if SENSITIVE.search(ast.unparse(tree)):
        return False
    try:
        imports_safe = pure_imports(path, tree) and pure_fixtures(path)
    except (OSError, UnicodeError, SyntaxError, KeyError):
        return False
    if not imports_safe:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(
                module.startswith(("tests.", "test_", "conftest", "smoke_utils"))
                for module in modules
            ) or (isinstance(node, ast.ImportFrom) and node.level):
                return False
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.decorator_list:
                decorators = " ".join(ast.unparse(d) for d in node.decorator_list)
                if "fixture" in decorators:
                    return False
            if node.name.startswith("test_"):
                if node.args.vararg or node.args.kwarg:
                    return False
                parameters = {
                    a.arg
                    for a in (
                        node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                    )
                } - {"self"}
                parametrized: set[str] = set()
                for decorator in node.decorator_list:
                    if (
                        isinstance(decorator, ast.Call)
                        and ast.unparse(decorator.func) == "pytest.mark.parametrize"
                        and decorator.args
                    ):
                        if any(
                            keyword.arg == "indirect"
                            and not (
                                isinstance(keyword.value, ast.Constant)
                                and keyword.value.value is False
                            )
                            for keyword in decorator.keywords
                        ):
                            return False
                        argument = decorator.args[0]
                        if isinstance(argument, ast.Constant) and isinstance(
                            argument.value, str
                        ):
                            parametrized.update(
                                part.strip() for part in argument.value.split(",")
                            )
                        elif isinstance(argument, (ast.List, ast.Tuple)):
                            parametrized.update(
                                item.value
                                for item in argument.elts
                                if isinstance(item, ast.Constant)
                                and isinstance(item.value, str)
                            )
                if parameters - PURE_FIXTURES - parametrized:
                    return False
    return True


@cache
def generic_modules(root: Path) -> frozenset[str]:
    return frozenset(
        path.name for path in (root / "tests").glob("test_*.py") if generic_test(path)
    )


SINGLE_OS_MODULES = generic_modules(Path(__file__).resolve().parents[1])


def test_tier(path: str) -> str:
    return "integration" if Path(path).name in INTEGRATION_MODULES else "core"


def module_shard(path: str, count: int) -> int:
    """Keep module fixtures together and make assignment stable across hosts."""
    digest = hashlib.sha256(Path(path).name.encode()).digest()
    return int.from_bytes(digest[:8], "little") % count


@cache
def shared_fake_data(path: Path) -> bool:
    """Whether a module uses the shared synthetic data (see xdist scheduler)."""
    try:
        tree = ast.parse(path.read_bytes(), str(path))
    except (OSError, SyntaxError):
        return False
    return any(
        isinstance(node, ast.ImportFrom)
        and (node.module, node.level) in {("tests.smoke_utils", 0), ("smoke_utils", 1)}
        and any(alias.name == "ensure_fake_data" for alias in node.names)
        for node in tree.body
    )
