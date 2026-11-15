"""``ref test-genome``: report, install or delete the test genome in the
dataset library."""

import argparse

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.ref import RefTestGenomeParams
from wgsextract_cli.core import library_removal, mini_genome, read_scope
from wgsextract_cli.core.download_progress import format_bytes
from wgsextract_cli.core.results import current_result, human_output
from wgsextract_cli.core.utils import WGSExtractError

from .status import dataset_library_for


def run_test_genome(params: RefTestGenomeParams) -> None:
    datasets = dataset_library_for(params.ref, params.dataset_library)
    extra: dict[str, object] = {}
    if params.install or params.delete:
        if not read_scope.may_follow(datasets):
            raise WGSExtractError(
                f"{datasets} leads outside the folders this server may use; "
                "not changing it."
            )
    if params.install:
        extra["changed"] = mini_genome.install(datasets)
    elif params.delete:
        deleted = library_removal.remove_test_genome(datasets)
        freed = sum(deleted.values())
        extra = {"deleted": [str(path) for path in deleted], "bytes_freed": freed}
        human_output(
            f"Deleted the test genome ({format_bytes(freed)}) from {datasets}."
            if deleted
            else f"No test genome in {datasets}; nothing to delete."
        )
    state = mini_genome.status(datasets)
    current_result().set_details({"library": str(datasets), **state, **extra})
    if not params.delete:
        human_output(
            "WGS Extract hg19 mini benchmark genome: "
            f"{state['status']} ({state['path']})"
        )


def cmd_test_genome(args: argparse.Namespace) -> None:
    # As `ref status`: only an explicit --ref outranks config reference_library.
    if "ref" not in getattr(args, "_explicit_dests", set()):
        args.ref = None
    _cli_test_genome(args)


_cli_test_genome = cli_handler(RefTestGenomeParams, run_test_genome)
