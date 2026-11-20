from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.ref import RefCytobandsParams
from wgsextract_cli.core.cytobands import install_cytobands
from wgsextract_cli.core.results import current_result, human_output


def run_cytobands(params: RefCytobandsParams) -> None:
    installed = install_cytobands(
        params.library(),
        params.build,
        download=params.download,
        cancel_event=cancel_event(),
    )
    current_result().add_output(installed, "reference_resource")
    human_output(f"Installed cytobands: {installed}")


cmd_cytobands = cli_handler(RefCytobandsParams, run_cytobands)
