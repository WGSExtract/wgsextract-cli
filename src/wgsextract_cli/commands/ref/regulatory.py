import logging

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.ref import RefRegulatoryParams
from wgsextract_cli.core import regulatory_data
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.utils import UsageError, WGSExtractError


def run_regulatory(params: RefRegulatoryParams) -> None:
    if params.download and params.bed is not None:
        raise UsageError("Choose --download or --bed, not both.")
    table = regulatory_data.path(str(params.library()))
    source = regulatory_data.provenance(table, verify=True)
    publish = params.bed is not None or (
        params.download and (not source or source["origin"] != "upstream")
    )
    if publish:
        verify_dependencies(["bgzip", "tabix"])
        regulatory_data.install(table, params.bed, cancel=cancel_event())
        source = regulatory_data.provenance(table)
    if source is None:
        raise WGSExtractError(regulatory_data.missing(table))

    logging.info("%s: %s (%s)", regulatory_data.LABEL, table, source["origin"])
    result = current_result()
    result.set_details({"regulatory_source": source})
    record = result.add_output if publish else result.add_input
    record(table, "regulatory_track")
    record(regulatory_data.index(table), "regulatory_track_index")
    record(regulatory_data.marker(table), "regulatory_provenance")


cmd_regulatory = cli_handler(RefRegulatoryParams, run_regulatory)
