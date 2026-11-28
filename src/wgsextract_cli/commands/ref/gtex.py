import logging

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.ref import RefGtexParams
from wgsextract_cli.core import gtex_data, resource_releases
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.utils import WGSExtractError


def run_gtex(params: RefGtexParams) -> None:
    table = gtex_data.path(str(params.library()))
    published = False
    try:
        with gtex_data.open_track(table, verify=True) as (_, source):
            pass
    except WGSExtractError:
        if not params.download:
            raise
        gtex_data.install(table, cancel=cancel_event())
        with gtex_data.open_track(table) as (_, source):
            pass
        published = True
    logging.info("%s: %s", gtex_data.LABEL, table)
    result = current_result()
    result.set_details({"gtex_source": source})
    resource_releases.record_pinned("gtex", gtex_data.BUILD, table)
    record = result.add_output if published else result.add_input
    record(table, "gtex_track")


cmd_gtex = cli_handler(RefGtexParams, run_gtex)
