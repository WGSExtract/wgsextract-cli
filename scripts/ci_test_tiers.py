"""Explicit slow-module partition; unclassified/new tests stay in the core tier."""

from pathlib import Path

# These suites exercise real workflows, resumability and multiple tool stages.
# Keep whole modules together: their module fixtures are intentionally shared.
INTEGRATION_MODULES = frozenset(
    {
        "test_realign.py",
        "test_analyze_workflows.py",
        "test_benchmark.py",
        "test_benchmark_modules.py",
        "test_pipeline_interop.py",
        "test_contig_calling.py",
        "test_microarray_reuse.py",
        "test_native_aligners.py",
        "test_deepvariant.py",
    }
)

# Pure genotype parsing and repeat classification; real genotyping commands
# remain cross-platform in test_genotyping_commands.py.
SINGLE_OS_MODULES = frozenset({"test_genotyping.py"})


def test_tier(path: str) -> str:
    return "integration" if Path(path).name in INTEGRATION_MODULES else "core"
