"""``report-delta``: what changed scientifically between two runs."""

from wgsextract_cli.api.report import ReportDeltaParams
from wgsextract_cli.core import run_delta as semantic_delta
from wgsextract_cli.core.results import current_result, human_output


def run_delta(params: ReportDeltaParams) -> None:
    before = params.before.expanduser().absolute()
    after = params.after.expanduser().absolute()
    delta = semantic_delta.compare(before, after)
    result = current_result()
    result.add_input(before, "before_manifest")
    result.add_input(after, "after_manifest")
    summary = delta["summary"]
    result.add_metrics(**summary)
    if not (
        delta["before"]["resources_recorded"] and delta["after"]["resources_recorded"]
    ):
        result.warn(
            "resources_not_recorded",
            "A manifest predates recording resource releases; releases are not compared.",
        )
    result.set_details(dict(delta))
    for line in semantic_delta.human_lines(delta):
        human_output(line)
