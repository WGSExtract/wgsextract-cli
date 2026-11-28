from collections.abc import Mapping
from pathlib import Path

from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.vcf import VcfGtexParams
from wgsextract_cli.core import gtex_data, gtex_matches, resource_releases
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.paths import same_path
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.core.vcf_build import require_grch38

from .annotation_helpers import annotation_context
from .basic import vcf_input_handler

GTEX_TSV = "gtex_eqtl.tsv"
GTEX_PAIRS_TSV = "gtex_eqtl_pairs.tsv"


def input_generations(
    inputs: Mapping[str, str | Path],
) -> dict[str, tuple[int, int, int, int, int] | None]:
    result: dict[str, tuple[int, int, int, int, int] | None] = {}
    for role, path in inputs.items():
        try:
            info = Path(path).stat()
        except FileNotFoundError:
            result[role] = None
        else:
            result[role] = (
                info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
            )  # fmt: skip
    return result


def protect_output(output: Path, *inputs: str | Path | None) -> None:
    resolved = output.resolve()
    if any(
        path is not None
        and (resolved == Path(path).resolve() or same_path(output, path))
        for path in inputs
    ):
        raise UsageError(
            f"{output.name} would overwrite an input; choose another --outdir."
        )


def record_sidecar_source(
    table: Path, source: dict[str, str], metrics: dict[str, int], pairs: str | None
) -> None:
    result = current_result()
    result.set_details({"gtex_source": source})
    result.add_metrics(**metrics)
    result.add_input(table, "gtex_track")
    result.add_input(gtex_data.receipt_path(table), "gtex_track_provenance")
    resource_releases.record_pinned(gtex_data.resource(pairs), gtex_data.BUILD, table)


def write_sidecar(
    input_file: str, table: Path, staged: Path, destination: Path, pairs: str | None
) -> dict[str, int]:
    with gtex_data.open_track(
        table, pairs=pairs, verify=True, cancel=cancel_event()
    ) as (
        db,
        source,
    ):
        metrics = gtex_matches.write_matches(
            input_file, staged, destination, db, pairs=pairs is not None
        )
    record_sidecar_source(table, source, metrics, pairs)
    return metrics


def run_gtex(params: VcfGtexParams) -> None:
    verify_dependencies(["bcftools"])
    pairs = params.pairs
    filename = GTEX_TSV if pairs is None else GTEX_PAIRS_TSV
    input_file, outdir, lib = annotation_context(params, filename)
    require_grch38(input_file, "GTEx")
    table, output = gtex_data.path(lib.root, pairs), Path(outdir, filename)
    protect_output(output, input_file, table, gtex_data.receipt_path(table))
    with atomic_output(output, retire_indexes=False) as staged:
        write_sidecar(input_file, table, Path(staged), output, pairs)
    current_result().add_output(output, "gtex_eqtl_associations")


cmd_gtex = vcf_input_handler(VcfGtexParams, run_gtex)
