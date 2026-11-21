import argparse
import shlex

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.ref import RefCytobandsParams
from wgsextract_cli.core.config import config_args
from wgsextract_cli.core.cytobands import install_cytobands
from wgsextract_cli.core.results import current_result, human_output


def run_cytobands(params: RefCytobandsParams) -> None:
    installed, published = install_cytobands(
        params.library(),
        params.build,
        download=params.download,
        cancel_event=cancel_event(),
        repair_command=shlex.join(
            [
                "wgsextract",
                *config_args(),
                "ref",
                "cytobands",
                "--build",
                params.build,
                "--ref",
                str(params.ref or params.library()),
                "--download",
            ]
        ),
    )
    if published:
        current_result().add_output(installed, "reference_resource")
    else:
        current_result().add_input(installed, "reference_resource")
    human_output(f"Installed cytobands: {installed}")


_cli_cytobands = cli_handler(RefCytobandsParams, run_cytobands)


def cmd_cytobands(args: argparse.Namespace) -> None:
    # The global parser's reference_fasta default is not this resource's library.
    if "ref" not in getattr(args, "_explicit_dests", set()):
        args.ref = None
    _cli_cytobands(args)
