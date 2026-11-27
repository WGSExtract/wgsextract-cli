import subprocess
from io import TextIOWrapper
from pathlib import Path

from wgsextract_cli.api.vcf import VcfRegulatoryParams
from wgsextract_cli.core import regulatory_data, regulatory_overlap
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.utils import UsageError, WGSExtractError, popen, run_command

from .annotation_helpers import annotation_context, record_annotated
from .basic import vcf_input_handler

REGULATORY_ANNOTATED = "regulatory_annotated.vcf.gz"


def _annotate(
    input_file: str,
    staged: str,
    table: Path,
    source: dict[str, str],
    threads: int | None,
) -> None:
    compress = ["bgzip", "-c"]
    if threads is not None:
        compress += ["-@", str(threads)]
    with (
        open(staged, "wb") as sink,
        popen(
            ["bcftools", "view", "--no-version", input_file],
            stdout=subprocess.PIPE,
        ) as reader,
        popen(compress, stdin=subprocess.PIPE, stdout=sink) as writer,
    ):
        assert reader.stdout is not None and writer.stdin is not None
        try:
            with TextIOWrapper(
                reader.stdout, encoding="utf-8", errors="surrogateescape"
            ) as lines:
                for line in regulatory_overlap.annotate_lines(lines, table, source):
                    writer.stdin.write(line.encode("utf-8", errors="surrogateescape"))
            writer.stdin.close()
            if reader.wait() != 0 or writer.wait() != 0:
                raise WGSExtractError(
                    "Could not read the VCF or compress its annotation output."
                )
        except BaseException:
            reader.kill()
            writer.kill()
            raise
    run_command(["tabix", "-p", "vcf", staged])


def run_regulatory(params: VcfRegulatoryParams) -> None:
    verify_dependencies(["bcftools", "bgzip", "tabix"])
    input_file, outdir, lib = annotation_context(params, REGULATORY_ANNOTATED)
    regulatory_overlap.require_grch38(input_file)
    table = regulatory_data.path(lib.root)
    final = Path(outdir, REGULATORY_ANNOTATED)
    if absolute_path(input_file) == absolute_path(final):
        raise UsageError(
            "vcf regulatory would overwrite its input; choose another --outdir."
        )
    with regulatory_data.snapshot(table, final.parent) as (track, source):
        with atomic_output(final) as staged:
            _annotate(input_file, staged, track, source, params.threads)
    result = current_result()
    result.set_details({"regulatory_source": source})
    result.add_input(table, "regulatory_track")
    result.add_input(regulatory_data.index(table), "regulatory_track_index")
    record_annotated(str(final))


cmd_regulatory = vcf_input_handler(VcfRegulatoryParams, run_regulatory)
