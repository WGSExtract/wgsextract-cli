from collections.abc import Mapping
from pathlib import Path

from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.vcf import VcfGtexParams
from wgsextract_cli.core import gtex_data, gtex_matches, resource_releases
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.paths import same_path
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.step_identity import NotARegularFile, file_drift, file_identity
from wgsextract_cli.core.steps import Step, StepJournal
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.core.vcf_build import require_grch38

from .annotation_helpers import annotation_context
from .basic import vcf_input_handler

GTEX_TSV = "gtex_eqtl.tsv"


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


def _owns_chain_sidecar(journal: StepJournal, sidecar: Path) -> bool:
    if sidecar.is_symlink() or not sidecar.is_file():
        return False
    try:
        current = file_identity(sidecar)
        generation = input_generations({"tsv": sidecar})["tsv"]
    except (OSError, NotARegularFile):
        return False
    return generation is not None and any(
        not item.get("retired")
        and isinstance(item.get("path"), str)
        and journal.resolve(item["path"]) == sidecar
        and item.get("generation") == list(generation[:4])
        and file_drift(item, current) is None
        for item in journal.check(Step("gtex")).outputs
    )


class ChainSidecarGuard:
    def __init__(
        self, journal: StepJournal, path: Path, inputs: list[str | Path | None]
    ) -> None:
        self.path, self.inputs = path, inputs
        self.generation = input_generations({"sidecar": path})["sidecar"]
        self.owned = _owns_chain_sidecar(journal, path)

    def check(self) -> None:
        protect_output(self.path, *self.inputs)
        if input_generations({"sidecar": self.path})["sidecar"] != self.generation:
            raise UsageError(f"{self.path.name} changed during the chain; rerun it.")

    def validate_backup(self, backup: Path) -> None:
        actual = input_generations({"backup": backup})["backup"]
        if (
            backup.is_symlink()
            or self.generation is None
            or actual is None
            or actual[:4] != self.generation[:4]
        ):
            raise UsageError(
                f"{self.path.name} changed during publication; rerun the chain."
            )


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
    table: Path, source: dict[str, str], metrics: dict[str, int]
) -> None:
    result = current_result()
    result.set_details({"gtex_source": source})
    result.add_metrics(**metrics)
    result.add_input(table, "gtex_track")
    result.add_input(gtex_data.receipt_path(table), "gtex_track_provenance")
    resource_releases.record_pinned("gtex", gtex_data.BUILD, table)


def write_sidecar(input_file: str, table: Path, staged: Path) -> dict[str, int]:
    with gtex_data.open_track(table, verify=True, cancel=cancel_event()) as (
        db,
        source,
    ):
        metrics = gtex_matches.write_matches(input_file, staged, db)
    record_sidecar_source(table, source, metrics)
    return metrics


def run_gtex(params: VcfGtexParams) -> None:
    verify_dependencies(["bcftools"])
    input_file, outdir, lib = annotation_context(params, GTEX_TSV)
    require_grch38(input_file, "GTEx")
    table, output = gtex_data.path(lib.root), Path(outdir, GTEX_TSV)
    protect_output(output, input_file, table, gtex_data.receipt_path(table))
    with atomic_output(output, retire_indexes=False) as staged:
        write_sidecar(input_file, table, Path(staged))
    current_result().add_output(output, "gtex_eqtl_associations")


cmd_gtex = vcf_input_handler(VcfGtexParams, run_gtex)
