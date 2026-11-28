import logging

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.ref import RefGtexParams
from wgsextract_cli.core import gtex_data, resource_releases
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.utils import WGSExtractError


def run_gtex(params: RefGtexParams) -> None:
    pairs = params.pairs
    table = gtex_data.path(str(params.library()), pairs)
    cancel = cancel_event()
    published = False
    try:
        with gtex_data.open_track(table, pairs=pairs, verify=True, cancel=cancel) as (
            _,
            source,
        ):
            pass
    except WGSExtractError:
        if not params.download or (cancel and cancel.is_set()):
            raise
        gtex_data.install(table, pairs=pairs, cancel=cancel)
        with gtex_data.open_track(table, pairs=pairs) as (_, source):
            pass
        published = True
    logging.info("%s: %s", gtex_data.label(pairs), table)
    result = current_result()
    result.set_details({"gtex_source": source})
    resource_releases.record_pinned(gtex_data.resource(pairs), gtex_data.BUILD, table)
    record = result.add_output if published else result.add_input
    record(table, "gtex_track")
    record(gtex_data.receipt_path(table), "gtex_track_provenance")


cmd_gtex = cli_handler(RefGtexParams, run_gtex)
