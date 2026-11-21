"""Where the coverage commands' reports live, and their breadth."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wgsextract_cli.api.info import CalculateCoverageParams
from wgsextract_cli.core.results import current_result, human_output

from .render import (
    existing_full_coverage,
    is_fresh_output,
    region_file_tag,
    run_full_coverage,
    run_sampled_coverage,
)


def coverage_map(
    info_cmd: str | None,
    input_p: str,
    resolved_ref: str | None,
    idx_stats: list[Any],
    outdir: str,
    region: str | None,
    n_lengths: Mapping[str, int],
) -> dict[str, str]:
    lengths = {s["name"]: s["length"] for s in idx_stats if s["name"] != "*"}
    if info_cmd is None:
        # A whole-file `calculate-coverage` result newer than the input adds
        # its breadth to `info --detailed`; nothing is computed for it.
        if region:
            return {}
        path = coverage_path("calculate-coverage", input_p, outdir, None)
        return existing_full_coverage(input_p, path, n_lengths, lengths)
    if info_cmd not in ("calculate-coverage", "coverage-sample"):
        return {}
    path = coverage_path(info_cmd, input_p, outdir, region)
    stem = str(Path(outdir, Path(input_p).name))
    if info_cmd == "calculate-coverage":
        return run_full_coverage(
            input_p,
            resolved_ref,
            path,
            region=region,
            # Whole-file results from older releases (samtools depth | awk).
            legacy_p=None if region else f"{stem}_bincvg.csv",
            n_lengths=n_lengths,
            contig_lengths=lengths,
        )
    return run_sampled_coverage(
        input_p, resolved_ref, idx_stats, path, region, n_lengths=n_lengths
    )


def coverage_path(
    info_cmd: str | None, input_p: str, outdir: str, region: str | None
) -> str:
    stem = str(Path(outdir, Path(input_p).name)) + region_file_tag(region)
    if info_cmd == "calculate-coverage":
        return f"{stem}_coverage.tsv"
    return f"{stem}_samplecvg.json"


def coverage_status(params: CalculateCoverageParams) -> None:
    """Whether the whole-file reports ``--with-bins`` writes exist and are newer
    than the input, so a run would show them again instead of rescanning."""
    reports: dict[str, Path] = {}
    if params.input:
        source = Path(params.input).expanduser()
        folder = Path(params.outdir).expanduser() if params.outdir else source.parent
        reports = {
            role: folder / f"{source.name}{suffix}"
            for role, suffix in (
                ("coverage", "_coverage.tsv"),
                ("coverage_bins", "_coverage_bins.tsv"),
            )
        }
    from .bins import bins_report_current

    current = (
        bool(reports)
        and is_fresh_output(str(reports["coverage"]), str(source))
        and bins_report_current(reports["coverage_bins"], str(source))
    )
    current_result().set_details(
        {"current": current, **{role: str(path) for role, path in reports.items()}}
    )
    human_output(
        "Coverage reports: "
        + ("current" if current else "not current")
        + "".join(f"\n  {path}" for path in reports.values())
    )
