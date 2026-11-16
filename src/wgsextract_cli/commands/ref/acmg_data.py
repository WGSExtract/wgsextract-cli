"""``ref acmg-data``: the sources and fastVEP databases ``vcf acmg`` reads."""

import logging
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._runtime import cancel_event
from wgsextract_cli.api.ref import RefAcmgDataParams
from wgsextract_cli.core import acmg_data, read_scope
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.utils import UsageError

TOOLS = ["fastvep", "bcftools", "bgzip", "tabix"]


def run_acmg_data(params: RefAcmgDataParams) -> None:
    reflib = params.library()
    sa_dir = params.databases()
    # Checked again as the run starts, links resolved: a server checked them
    # when the job was submitted.
    folders, files = acmg_data.destinations(reflib, sa_dir, params.ensembl_release)
    for path in (*folders, *files):
        if not read_scope.may_follow(path):
            raise UsageError(f"{path} is outside the folders this run may use.")
    spliceai = None
    if params.spliceai_file:
        spliceai = Path(expand_path(params.spliceai_file))
        if not spliceai.is_file():
            raise UsageError(f"--spliceai-file {spliceai} doesn't exist.")
    # Chromosomes built at a time: gnomAD's streams are network-bound.
    workers = int(params.threads) if params.threads else 4
    plan = acmg_data.make_plan(
        reflib,
        sa_dir,
        acmg_data.parse_chromosomes(params.chromosomes),
        params.ensembl_release,
        spliceai,
    )
    for line in acmg_data.describe(plan, workers):
        human_output(line)
    restricted = [s for s in acmg_data.SOURCES if s.restricted]
    warn(
        "acmg_data_terms",
        "You download these sources yourself, under their publishers' terms; "
        + " ".join(s.terms for s in restricted)
        + " Don't share the databases built from them.",
        sources=",".join(s.key for s in restricted),
    )
    result = current_result()
    result.set_details(
        {
            "sa_dir": str(sa_dir),
            "gff3": str(plan.gff3),
            "chromosomes": plan.chromosomes,
            "built": plan.done,
            "to_build": [
                {
                    "name": s.name,
                    "source": s.source,
                    "chromosome": s.chromosome,
                    "download_bytes": s.download,
                    "stream_bytes": s.stream,
                    "database_bytes": s.database,
                }
                for s in plan.steps
            ],
            "downloads": plan.downloads,
            "needed_bytes": plan.need_bytes(workers),
            "terms": {s.key: s.terms for s in acmg_data.SOURCES},
        }
    )
    if params.dry_run:
        return
    if not plan.steps:
        human_output("Everything is built; nothing to do.")
        return
    # Only a build records its outputs there: a dry run or a run with nothing
    # to do would replace the last build's inventory with an empty one.
    result.set_manifest_stem(acmg_data.folder(reflib) / "acmg_data")
    verify_dependencies(TOOLS)
    log_dependency_info(TOOLS)
    acmg_data.fastvep_version()
    acmg_data.check_disk(plan, workers)
    acmg_data.build(
        plan,
        workers,
        cancel_event(),
        lambda path: result.add_output(path, "reference_resource"),
    )
    result.add_output(sa_dir / acmg_data.MANIFEST, "reference_resource")
    logging.info("The ACMG databases are built in %s.", sa_dir)


cmd_acmg_data = cli_handler(RefAcmgDataParams, run_acmg_data)
