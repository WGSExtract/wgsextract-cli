"""`methylation regions`: offline aggregation of precomputed bedMethyl."""

import json
from pathlib import Path

from wgsextract_cli.api.methylation import MethylationRegionsParams
from wgsextract_cli.core.bgzf import is_bgzf
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.methylation_annotations import (
    BUILDS,
    chromosomes,
    load_regions,
)
from wgsextract_cli.core.methylation_regions import (
    REGION_TABLE,
    read_sites,
    reference_cpgs,
    region_rows,
    resolve_contigs,
)
from wgsextract_cli.core.paths import absolute_path, append_suffix
from wgsextract_cli.core.ref_library.assets import read_fai_lengths
from wgsextract_cli.core.results import current_result, human_output
from wgsextract_cli.core.table_writer import (
    check_format,
    provenance,
    require_pyarrow,
    table_path,
    write_table,
)
from wgsextract_cli.core.temp_directory import scratch_directory
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.core.variant_files import verify_paths_exist


def run_regions(params: MethylationRegionsParams) -> None:
    path = absolute_path(params.input.expanduser())
    fasta = absolute_path(params.ref.expanduser())
    verify_paths_exist({"--input": path, "--ref": fasta})
    if not path.is_file() or path.suffix.lower() in (".bam", ".cram"):
        raise UsageError("--input must be precomputed, strand-combined CpG bedMethyl.")
    if not fasta.is_file():
        raise UsageError("--ref must be a local FASTA file, not a directory.")
    with fasta.open("rb") as handle:
        compressed = handle.read(2) == b"\x1f\x8b"
    if compressed and not is_bgzf(str(fasta)):
        raise UsageError("--ref must be uncompressed or BGZF FASTA, not ordinary gzip.")
    lengths = read_fai_lengths(fasta)
    if not lengths or (compressed and not append_suffix(fasta, ".gzi").is_file()):
        raise UsageError(
            f"--ref needs a samtools faidx index: {fasta}. "
            "Use uncompressed or BGZF FASTA."
        )
    specs = chromosomes(params.build)
    accession_stems = {
        name.partition(".")[0]
        for build in BUILDS
        for chrom in chromosomes(build)
        for name in chrom.aliases
        if "." in name
    }
    aliases, reference = resolve_contigs(specs, lengths, params.build, accession_stems)
    check_format(params.table_format)
    if path.suffix.lower() == ".parquet":
        require_pyarrow()
    verify_dependencies(["samtools"])
    regions, source = load_regions(
        params.build,
        params.preset,
        {chrom.name: chrom.length for chrom in specs},
        params.upstream,
        params.downstream,
    )
    codes = ("m", "h") if params.hydroxymethyl else ("m",)
    outdir = absolute_path((params.outdir or path.parent).expanduser())
    outdir.mkdir(parents=True, exist_ok=True)
    name = path.name
    for suffix in (".gz", ".bgz", ".bgzf", ".parquet", ".bedmethyl", ".bed"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    stem = outdir / f"{name}.{params.build}.{params.preset}.methylation"
    output = table_path(stem, params.table_format)
    result = current_result()
    result.add_input(path, "bedmethyl")
    result.set_reference(fasta, params.build)
    detail = {
        "annotation": source,
        "preset": params.preset,
        "min_valid_calls": params.min_valid_calls,
        "min_cpgs": params.min_cpgs,
        "cpg_rule": "reference CpG C anchor in 0-based half-open region",
        "mod_codes": list(codes),
    }
    if params.preset == "promoters":
        detail.update(upstream=params.upstream, downstream=params.downstream)
    metadata = provenance(
        [path], reference_build=params.build, region_summary=json.dumps(detail)
    )
    with scratch_directory(outdir, ".wgse_methylation_regions_") as temporary:
        cpgs = reference_cpgs(fasta, reference, lengths, regions, Path(temporary))
        rows = region_rows(
            read_sites(path, params.build),
            regions,
            aliases,
            lengths,
            cpgs,
            codes,
            params.min_valid_calls,
            params.min_cpgs,
        )
        row_count = write_table(
            output, REGION_TABLE, rows, params.table_format, metadata=metadata
        )
    result.set_details(detail)
    result.add_output(output, "methylation_regions", metrics={"rows": row_count})
    result.set_manifest_stem(stem)
    result.add_metrics(regions=len(regions))
    human_output(
        f"Wrote {len(regions):,} methylation regions ({params.preset}): {output}"
    )
