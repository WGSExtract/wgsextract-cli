"""Help entries for ``benchmark``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "benchmark": _h(
        "runs/<timestamp>/ under --outdir (default out/benchmark): "
        "benchmark_report.md and benchmark_results.json (timings, tool versions) "
        "and per-step logs.",
        "wgsextract benchmark --profile smoke",
    ),
}
